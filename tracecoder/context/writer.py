"""会话事件写入 helper。

JSONL store 的底层接口故意保持简单，只负责 append/list/rebuild。上层如果到处手写
payload，tool_call/tool_result 这类 provider 协议边界很容易漂移；writer 把常见事件写入
集中起来，后续正式事件 schema 也优先在这里演进。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping, Sequence

from tracecoder.context.compaction import CompactionEvent
from tracecoder.context.events import SessionEvent
from tracecoder.context.identity import new_event_id, new_message_id, new_part_id
from tracecoder.context.llm_compact import LlmCompactEvent
from tracecoder.context.metadata import metadata_without_reserved_keys
from tracecoder.context.models import MessagePart, utc_now_iso
from tracecoder.context.store import JsonlSessionStore
from tracecoder.context.task_boundary import TaskBoundaryObservation, TaskBoundaryService
from tracecoder.context.versions import CONTEXT_EVENT_SCHEMA_VERSION
from tracecoder.input.attachments import PreparedAttachment
from tracecoder.planning.models import TaskPlan
from tracecoder.planning.validation import validate_plan
from tracecoder.providers.types import ChatResponse, ToolCall
from tracecoder.tools.types import ToolResult


class SessionEventWriter:
    """为单个 session 追加结构化事件。

    writer 是所有消息事件落库前的最后一层公共入口，因此 turn 元数据在这里统一补齐。
    上层 session 可以继续维护自己的运行期状态，但不需要在每条消息写入前重复拼
    `created_turn` / `turn_id`，避免直接调用 writer 的路径漏掉上下文窗口判断需要的字段。
    """

    def __init__(self, *, store: JsonlSessionStore, session_id: str, current_turn: int = 0) -> None:
        """绑定单个会话的存储和 turn 计数；所有 append_* 最终都通过此 writer 写事件。"""
        self.store = store
        self.session_id = session_id
        self.current_turn = current_turn

    def append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """为 payload 分配事件 ID 和时间戳，再交给 store 追加到该 session 的 JSONL。"""
        self.store.append_event(
            SessionEvent(
                id=new_event_id(),
                session_id=self.session_id,
                type=event_type,
                payload=payload,
            )
        )

    def append_session_created(self, **metadata: Any) -> None:
        """写入会话起始元数据，并附上事件 schema 版本；这不是 provider 聊天消息。"""
        payload = {"session_id": self.session_id}
        payload.update(metadata_without_reserved_keys(metadata))
        payload["context_event_schema_version"] = CONTEXT_EVENT_SCHEMA_VERSION
        self.append_event("session_created", payload)

    def append_session_metadata_updated(self, **metadata: Any) -> None:
        """追加用户可见 session metadata patch。

        这个事件只影响 session catalog/share 等用户入口，不生成普通消息，也不进入
        provider context。
        """

        self.append_event("session_metadata_updated", metadata_without_reserved_keys(metadata))

    def append_agent_turn_telemetry(self, payload: dict[str, Any]) -> None:
        """持久化 agent 控制循环指标，不把它投影成 provider 消息。"""

        self.append_event("agent_turn_telemetry", dict(payload))

    def append_message_part_metadata_updated(self, *, message_id: str, part_id: str, metadata: dict[str, Any]) -> None:
        """追加 part 元数据补丁，不重写原消息；store 重放时将补丁合并到目标 part。"""
        self.append_event(
            "message_part_metadata_updated",
            {
                "message_id": message_id,
                "part_id": part_id,
                "metadata": dict(metadata),
            },
        )

    def append_provider_projection_consumed(
        self,
        *,
        request_id: str,
        projection_fingerprint: str,
        part_ids: list[str],
        provider: str,
        model: str,
    ) -> None:
        """记录某次成功 provider 请求实际看见的工具结果 parts，用于上下文消费追踪。"""
        normalized = sorted({part_id for part_id in part_ids if part_id})
        if not normalized:
            return
        self.append_event(
            "provider_projection_consumed",
            {
                "request_id": request_id,
                "projection_fingerprint": projection_fingerprint,
                "part_ids": normalized,
                "provider": provider,
                "model": model,
            },
        )

    def append_user_message(
        self,
        content: str,
        *,
        attachments: list[PreparedAttachment] | None = None,
        metadata: dict[str, Any] | None = None,
        part_metadata: dict[str, Any] | None = None,
    ) -> str:
        """递增用户 turn，生成消息 ID，并将正文和已暂存附件封装为 parts 后追加事件。"""
        self.current_turn += 1
        message_id = new_message_id()
        parts = [
            MessagePart(
                id=new_part_id(),
                message_id=message_id,
                kind="text",
                content=content,
                metadata=self._part_metadata(part_metadata),
            )
        ]
        for attachment in attachments or []:
            attachment_metadata = dict(part_metadata or {})
            attachment_metadata.update(
                {
                    "filename": attachment.filename,
                    "media_type": attachment.media_type,
                    "path": attachment.relative_path,
                    "bytes": attachment.size_bytes,
                    "sha256": attachment.sha256,
                    "source": attachment.source,
                }
            )
            parts.append(
                MessagePart(
                    id=new_part_id(),
                    message_id=message_id,
                    kind=attachment.kind,
                    content=(f"[image: {attachment.filename}]" if attachment.kind == "image" else attachment.inline_text or f"[file: {attachment.filename}]"),
                    metadata=self._part_metadata(attachment_metadata),
                )
            )
        self._append_message_event(
            "user_message",
            message_id=message_id,
            parts=parts,
            metadata=metadata,
        )
        return message_id

    def append_assistant_response(self, response: ChatResponse) -> str:
        """把模型文本和工具调用拆成 assistant parts，同时记录 provider/model/finish_reason。"""
        message_id = new_message_id()
        parts: list[MessagePart] = []
        if response.content:
            parts.append(
                MessagePart(
                    id=new_part_id(),
                    message_id=message_id,
                    kind="text",
                    content=response.content,
                    metadata=self._part_metadata(),
                )
            )
        for tool_call in response.tool_calls:
            parts.append(tool_call_to_part(message_id=message_id, tool_call=tool_call))
        self._attach_turn_metadata(parts)
        self._append_message_event(
            "assistant_message",
            message_id=message_id,
            parts=parts,
            metadata={
                "provider": response.provider,
                "model": response.model,
                "finish_reason": response.finish_reason,
            },
        )
        return message_id

    def append_assistant_parts(
        self,
        parts: list[MessagePart],
        *,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> str:
        """写入已经由 agent 层转换好的 assistant parts。"""

        message_id = message_id or new_message_id()
        self._attach_turn_metadata(parts)
        self._append_message_event(
            "assistant_message",
            message_id=message_id,
            parts=parts,
            metadata=metadata,
        )
        return message_id

    def append_tool_result(self, *, tool_call: ToolCall, result: ToolResult) -> str:
        """追加一条 role=tool 事件，并保存 tool_call_id 以和 assistant 请求准确配对。"""
        message_id = new_message_id()
        part = MessagePart(
            id=new_part_id(),
            message_id=message_id,
            kind="tool_result",
            content=result.content,
            metadata={
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "ok": result.ok,
                "data": result.data,
                "error": result.error,
            },
        )
        self._attach_turn_metadata([part])
        self._append_message_event("tool_result", message_id=message_id, parts=[part])
        return message_id

    def append_tool_result_part(self, part: MessagePart, *, message_id: str | None = None) -> str:
        """写入已经由 agent 层转换好的 tool_result part。"""

        message_id = message_id or part.message_id
        self._attach_turn_metadata([part])
        self._append_message_event("tool_result", message_id=message_id, parts=[part])
        return message_id

    def append_compaction_completed(
        self,
        *,
        trigger: str,
        target_tokens: int,
        event: CompactionEvent,
    ) -> None:
        """记录规则/本地压缩结果及替换映射；store 重放该事件时会更新对应历史 parts。"""
        event_payload = asdict(event)
        self.append_event(
            "compaction_completed",
            {
                "event_version": CONTEXT_EVENT_SCHEMA_VERSION,
                "trigger": trigger,
                "target_tokens": target_tokens,
                "created_at": event.created_at,
                "input_fingerprint": event.input_fingerprint,
                "status": "success" if event.success else "failed",
                "reason": event.reason,
                "before_tokens": event.before_tokens,
                "after_tokens": event.after_tokens,
                "checkpoint_id": event.checkpoint_id,
                "event": event_payload,
            },
        )

    def append_llm_compaction_completed(
        self,
        *,
        trigger: str,
        target_tokens: int,
        event: LlmCompactEvent,
    ) -> None:
        """记录 LLM 摘要压缩结果；与普通 compaction 使用不同事件类型和结果字段。"""
        event_payload = asdict(event)
        created_at = utc_now_iso()
        self.append_event(
            "llm_compaction_completed",
            {
                "event_version": CONTEXT_EVENT_SCHEMA_VERSION,
                "trigger": trigger,
                "target_tokens": target_tokens,
                "created_at": created_at,
                "input_fingerprint": event.source_fingerprint,
                "status": event.status,
                "reason": event.failure_reason or event.status,
                "before_tokens": None,
                "after_tokens": None,
                "checkpoint_id": event.checkpoint_id,
                "event": event_payload,
            },
        )

    def append_compaction_skipped(self, *, trigger: str, input_fingerprint: str, reason: str) -> None:
        """记录本次为何未压缩，便于之后判断是预算无需压缩还是压缩条件不满足。"""
        self.append_event(
            "compaction_skipped",
            {
                "event_version": CONTEXT_EVENT_SCHEMA_VERSION,
                "trigger": trigger,
                "input_fingerprint": input_fingerprint,
                "reason": reason,
                "created_at": utc_now_iso(),
            },
        )

    def append_task_boundary_observation(self, observation: TaskBoundaryObservation) -> None:
        """把任务边界识别结果转换为 session 事件，供运行状态恢复和上下文标记使用。"""
        event = TaskBoundaryService().to_event(session_id=self.session_id, observation=observation)
        self.store.append_event(event)

    def append_task_plan_updated(
        self,
        *,
        previous_revision: int,
        operation: str,
        changes: Sequence[Mapping[str, object]],
        snapshot: TaskPlan | Mapping[str, object],
    ) -> None:
        """追加一次规划操作及其完整、已验证快照。"""

        if isinstance(previous_revision, bool) or not isinstance(previous_revision, int) or previous_revision < 0:
            raise ValueError("previous_revision must be a non-negative integer")
        if not isinstance(operation, str) or not operation.strip():
            raise ValueError("operation must be a non-blank string")

        plan = TaskPlan.from_dict(snapshot.to_dict() if isinstance(snapshot, TaskPlan) else snapshot)
        validate_plan(plan)
        if plan.revision != previous_revision + 1:
            raise ValueError("task plan revision must be exactly one greater than previous_revision")
        normalized_changes = [dict(change) for change in changes]
        self.append_event(
            "task_plan_updated",
            {
                "previous_revision": previous_revision,
                "revision": plan.revision,
                "operation": operation,
                "changes": normalized_changes,
                "snapshot": plan.to_dict(),
            },
        )

    def append_background_notification(
        self,
        *,
        content: str,
        job_id: str,
        tool_name: str,
        status: str,
        task_id: str | None = None,
        observed_revision: int | None = None,
    ) -> str:
        """追加一条后台任务完成通知，投影成普通 user 消息。

        通知不是某个 tool_call 的第二条结果：它以独立 user 文本进入历史，因此不会破坏
        provider 的 tool_call/tool_result 配对。这里刻意不递增 turn 计数，避免把后台完成
        误当成一次新的用户输入。
        """

        message_id = new_message_id()
        metadata = {
            "background_job_id": job_id,
            "background_tool_name": tool_name,
            "background_status": status,
        }
        if task_id is not None:
            metadata["background_task_id"] = task_id
        if observed_revision is not None:
            metadata["background_observed_revision"] = observed_revision
        part = MessagePart(
            id=new_part_id(),
            message_id=message_id,
            kind="text",
            content=content,
            metadata=self._part_metadata(metadata),
        )
        self._append_message_event(
            "background_notification",
            message_id=message_id,
            parts=[part],
        )
        return message_id

    def _append_message_event(
        self,
        event_type: str,
        *,
        message_id: str,
        parts: list[MessagePart],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """统一序列化 user/assistant/tool 消息事件，避免各调用点拼出不同 payload。"""
        self.append_event(
            event_type,
            {
                "message_id": message_id,
                "parts": [part.to_dict() for part in parts],
                "metadata": metadata or {},
            },
        )

    def _part_metadata(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """复制传入 metadata，并为新 part 补当前 turn 标记；调用方提供的值优先保留。"""
        merged = dict(metadata or {})
        merged.setdefault("created_turn", self.current_turn)
        merged.setdefault("turn_id", self.current_turn)
        return merged

    def _attach_turn_metadata(self, parts: list[MessagePart]) -> None:
        """给一批已经转换好的 parts 统一补 turn 元数据。"""
        for part in parts:
            part.metadata = self._part_metadata(part.metadata)


def tool_call_to_part(*, message_id: str, tool_call: ToolCall) -> MessagePart:
    """把 provider 的 ToolCall 转成持久化 part；ID、名称和参数保存在 metadata。"""
    return MessagePart(
        id=new_part_id(),
        message_id=message_id,
        kind="tool_call",
        content="",
        metadata={
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "arguments": tool_call.arguments,
        },
    )
