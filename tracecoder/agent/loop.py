"""TraceCoder 一轮用户请求的协调器：模型提出动作，程序执行，再把结果交还模型。

阅读路线（先走普通模式，再看流式与恢复分支）：
    run_user_turn
      -> _run_user_turn_sync -> _run_user_turn_sync_impl
      -> _run_tool_loop_interactive
           -> _complete_once_with_recovery -> _complete_once
           -> _continue_tool_loop_from_response
                -> ToolExecutor 执行工具并保存结果
                -> 再次调用 complete_once，直到没有工具调用或触发边界
           -> _complete_turn / _pending_turn_result

与 agent学习/最小实现/step02_agent_loop.py 对照：
    run_agent 的请求构造 -> _prepare_main_provider_request
    provider.complete   -> _complete_once
    for tool_call       -> _continue_tool_loop_from_response + ToolExecutor
    finish              -> _complete_turn

三个容易混淆的计数：
    user turn：一次用户输入引发的处理，权限确认恢复仍可属于同一 turn；
    provider call：实际模型请求次数，失败尝试和某些辅助调用也会消耗预算；
    tool round：一个模型响应中的一批工具调用，同批多个工具仍只算一轮。

职责边界：session 保存事实，ContextBuilder 构造模型可见消息，provider 适配模型协议，
ToolExecutor 管工具执行。本文件组织这些对象的调用顺序，不直接实现文件读写或模型 SDK。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

import anyio

from tracecoder.runtime.cancellation import AgentCancelledError, CancellationToken
from tracecoder.runtime.user_input import UserInputRequest
from tracecoder.agent.ports import ContextManagerLike
from tracecoder.agent.loop_limits import AgentLoopLimits, AgentLoopStopReason
from tracecoder.agent.provider_retry import DEFAULT_PROVIDER_RETRY_POLICY, ProviderRetryPolicy
from tracecoder.agent.execution_evidence import ExecutionEvidence, is_mutation_result
from tracecoder.agent.runtime_capabilities import AgentRuntimeCapabilities, PLANNING_TOOL_NAMES
from tracecoder.agent.session import AgentSession, PendingPermissionExecution
from tracecoder.agent.stagnation import StagnationGuard, append_guidance
from tracecoder.agent.telemetry import AgentTurnTelemetry
from tracecoder.agent.task_boundary_classifier import TaskBoundaryClassifier
from tracecoder.agent.task_plan_policy import TaskPlanPolicy, render_current_task_plan_snapshot
from tracecoder.agent.tool_execution import ToolExecutionEvent, ToolExecutor
from tracecoder.agent.tool_settlement import ToolCallSettlement
from tracecoder.agent.background import (
    DEFAULT_BACKGROUND_TOOL_NAMES,
    BackgroundJobManager,
    render_task_notification,
    with_background_controls,
)
from tracecoder.agent.user_input import (
    AgentTurnResult,
    AgentTurnStatus,
)
from tracecoder.context.context_builder import ContextBuilder
from tracecoder.context.identity import new_request_id, stable_json_hash
from tracecoder.context.manager import ContextCompactRequest, ContextWindowTrigger
from tracecoder.context.token_budget import ContextBudget, build_context_budget
from tracecoder.context.task_boundary import TaskBoundaryService
from tracecoder.input.attachments import UserAttachment
from tracecoder.permissions.types import PermissionDecision, PermissionDecisionKind, PermissionRequest
from tracecoder.providers.base import ChatProvider
from tracecoder.providers.errors import ProviderError, ProviderErrorKind
from tracecoder.providers.types import ChatMessage, ChatRequest, ChatResponse, ChatStreamEvent, MainRequestOptions, ToolCall
from tracecoder.tools.permission_results import (
    make_permission_denied_result,
    make_prewrite_review_failed_result,
    make_prewrite_review_stale_result,
)
from tracecoder.tools.background import create_background_cancel_tool, create_background_status_tool
from tracecoder.agent.subagent import SubagentRunner
from tracecoder.tools.delegate import create_delegate_tool
from tracecoder.tools.hidden import HIDDEN_TOOL_STATUS_NAMES
from tracecoder.tools.types import Tool, ToolResult, make_error_result


MAX_COMPLETION_GATE_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class PreparedMainRequest:
    """一次准备好的模型请求，以及记录“本次消费了哪些上下文”所需的元数据。

    request 发给模型；request_id 标识本次准备；projection_fingerprint 标识消息和工具快照；
    tool_result_part_ids 用于在成功取得响应后记录已被模型消费的工具结果。
    """

    request: ChatRequest
    request_id: str
    projection_fingerprint: str
    tool_result_part_ids: tuple[str, ...]


class AgentLoop:
    """把用户输入、上下文投影、provider 调用和工具执行串成一轮会话。

    可以把这一层理解成 TraceCoder 的“单轮事务”（不是数据库事务，失败不会自动回滚文件）：

    1. 先把用户输入写入 append-only session log。
    2. 从 session log 重建当前视图，投影成 provider messages。
    3. 调用模型。如果模型返回普通文本，就写入 assistant 消息并结束。
    4. 如果模型返回 tool_calls，就先写入 assistant tool_call，再执行工具。
    5. 工具结果写成 role=tool 消息后，再次调用模型，让模型基于工具结果继续回答。

    这里故意不把具体工具、OpenAI SDK chunk、Textual widget 混进来。AgentLoop 只协调
    “模型想做什么”和“会话事实应该怎样落库”，具体协议转换交给 provider/context 层。
    """

    def __init__(
        self,
        *,
        session: AgentSession,
        provider: ChatProvider,
        tools: list[Tool] | None = None,
        context_builder: ContextBuilder | None = None,
        context_manager: ContextManagerLike | None = None,
        limits: AgentLoopLimits | None = None,
        clock=time.monotonic,
        stream_event_handler: Callable[[ChatStreamEvent], None] | None = None,
        tool_event_handler: Callable[[ToolExecutionEvent], None] | None = None,
        guidance_provider: Callable[[], list[str]] | None = None,
        cancellation_token: CancellationToken | None = None,
        request_options: MainRequestOptions | None = None,
        context_window: int | None = None,
        background_manager: BackgroundJobManager | None = None,
        background_tool_names: frozenset[str] | None = None,
        enable_delegate_tool: bool | None = None,
        runtime_capabilities: AgentRuntimeCapabilities | None = None,
        provider_retry_policy: ProviderRetryPolicy | None = None,
        provider_retry_sleeper: Callable[[float], None] | None = None,
    ) -> None:
        """装配一轮 Agent 执行所需的协作者和策略。

        session、provider 是必需边界；tools 只补注册尚不存在的临时工具。
        context_builder/limits/retry policy 有默认值；context_manager、事件回调、取消、
        后台任务和子 Agent 能力由上层应用按运行模式注入。构造时会创建 ToolExecutor，
        并按配置补注册后台控制工具与 delegate。
        """
        # 先看这四个协作者即可：session 管历史、provider 问模型、context_builder 构造
        # 请求消息、tool_executor 执行工具。下面其余对象负责恢复、预算和运行策略。
        self.session = session
        self.tool_settlement = ToolCallSettlement(session)
        self.task_plan_policy = TaskPlanPolicy(session)
        self.provider = provider
        self.request_options = request_options or MainRequestOptions()
        self.context_window = context_window
        self.context_builder = context_builder or ContextBuilder()
        # builder 只投影；manager 决定是否压缩。manager=None 时不在这里自动压缩。
        self.context_manager = context_manager
        self.limits = limits or AgentLoopLimits.default()
        self.max_tool_rounds = self.limits.max_tool_rounds
        self.clock = clock
        # 预算时钟允许注入以控制超时判断；遥测另用单调时钟记录真实运行耗时。
        self.telemetry_clock = time.monotonic
        self.provider_call_count = 0
        self.turn_started_at: float | None = None
        self.telemetry_started_at: float | None = None
        self.last_stream_events: list[ChatStreamEvent] = []
        # 回调把运行事件交给 UI/调用方；它们不负责执行工具或决定历史怎样落盘。
        self.stream_event_handler = stream_event_handler
        self.tool_event_handler = tool_event_handler
        self.guidance_provider = guidance_provider
        self.cancellation_token = cancellation_token
        self.provider_retry_policy = provider_retry_policy or DEFAULT_PROVIDER_RETRY_POLICY
        self.provider_retry_sleeper = provider_retry_sleeper or time.sleep
        self.background_manager = background_manager
        # 能力配置决定本次运行启用哪些工具和护栏；交互与 benchmark 的默认配置不同。
        self.runtime_capabilities = runtime_capabilities or (
            AgentRuntimeCapabilities.benchmark(session.benchmark_task)
            if session.benchmark_task
            else AgentRuntimeCapabilities.interactive()
        )
        configured_background_names = self.runtime_capabilities.background_tool_names
        self.background_tool_names = (
            background_tool_names
            if background_tool_names is not None
            else configured_background_names or DEFAULT_BACKGROUND_TOOL_NAMES
        )
        self.enable_delegate_tool = (
            self.runtime_capabilities.enable_delegate_tool
            if enable_delegate_tool is None
            else enable_delegate_tool
        )
        # 收尾检查的次数和执行证据属于本轮状态，防止“检查完成情况”自身无限循环。
        self._task_plan_reconciliation_attempted = False
        self._completion_gate_attempted = False
        self._completion_gate_attempt_count = 0
        self._tool_rounds_completed = 0
        self.execution_evidence = ExecutionEvidence.for_task(session.benchmark_task)
        self.stagnation_guard = StagnationGuard()
        self.turn_telemetry = AgentTurnTelemetry()
        self.task_boundary_classifier = TaskBoundaryClassifier(
            session=session,
            provider=provider,
            context_builder=self.context_builder,
            compact_if_needed=self._compact_if_needed,
            check_cancelled=self._check_cancelled,
            reserve_provider_call=self._reserve_provider_call,
            check_turn_timeout=self._check_turn_timeout,
            tag_task_boundary_messages=self._tag_task_boundary_messages_with_active_hash,
        )
        # session 创建时通常已经注册了 session-scoped 工具。这里允许调用方再传入一批
        # 测试或临时工具，但避免重复注册同名工具导致模型 schema 不稳定。
        if tools:
            for tool in tools:
                if tool.name not in self.session.tool_registry.names():
                    self.session.tool_registry.register(tool)
        self._mcp_tool_names = {
            name
            for name in self.session.tool_registry.names()
            if name.startswith("mcp__")
        }
        self._active_mcp_tool_names: set[str] = set()
        # 把执行侧需要的回调注入 ToolExecutor。它负责权限、并行/后台调度、结果保存；
        # loop 只接收“需等待用户吗”“任务边界变化了吗”等批次级结果。
        self.tool_executor = ToolExecutor(
            session=session,
            settlement=self.tool_settlement,
            emit_event=self._emit_tool_event,
            check_cancelled=self._check_cancelled,
            cancellation_token=self.cancellation_token,
            tag_task_boundary_messages=self._tag_task_boundary_messages_with_active_hash,
            emit_settlements=self._emit_settlements,
            validate_tool_call=self._validate_tool_call,
            observe_tool_result=self._observe_tool_result,
            background_manager=self.background_manager,
            background_tool_names=self.background_tool_names,
        )
        self._ensure_background_control_tools()
        self._ensure_delegate_tool()

    async def run_user_turn(
        self,
        content: str,
        *,
        attachments: list[UserAttachment] | None = None,
        streaming: bool = False,
    ) -> AgentTurnResult:
        """处理一条用户输入，对外统一返回 AgentTurnResult。

        例如 await loop.run_user_turn("读取配置", streaming=False)。一次输入可能引发
        多次模型调用和多轮工具执行；返回值可能是最终响应，也可能是等待用户确认的请求。
        streaming 决定模型响应的接收方式，两个分支遵循同样的工具调用顺序。
        """

        if streaming:
            return await self._run_user_turn_streaming(content, attachments=attachments)
        # 普通 provider/工具有同步阻塞调用，把同步实现交给线程，避免阻塞上层事件循环。
        # lambda 是交给线程执行的函数，此处不是提前执行 _run_user_turn_sync。
        return await anyio.to_thread.run_sync(
            lambda: self._run_user_turn_sync(content, attachments=attachments)
        )

    def replace_cancellation_token(self, token: CancellationToken | None) -> None:
        """恢复暂停任务时，同步更新 loop 和工具执行器使用的协作取消信号。"""

        self.cancellation_token = token
        self.tool_executor.cancellation_token = token

    def clear_stream_events(self) -> None:
        """清空上一轮留给 UI 读取的 stream 事件，避免把旧 delta 展示到新一轮。"""
        self.last_stream_events = []

    def _validate_attachments(self, attachments: list[UserAttachment] | None) -> None:
        """在消息落库前确认 provider 能消费本轮附件。

        文件附件可以继续以内联文本或路径引用进入上下文；图片则必须由当前模型显式声明
        视觉能力，避免 provider 静默忽略图片后让 agent 在错误前提下执行任务。
        """

        if not attachments or not any(item.kind == "image" for item in attachments):
            return
        capabilities = getattr(self.provider, "capabilities", None)
        if bool(getattr(capabilities, "supports_vision", False)):
            return
        raise ValueError(
            f"模型 {self.provider.name}/{self.provider.model} 未声明视觉能力，"
            "无法发送图片附件；请在对应 [models] 配置中设置 vision = true。"
        )

    def _run_user_turn_sync(
        self,
        content: str,
        *,
        attachments: list[UserAttachment] | None = None,
    ) -> AgentTurnResult:
        """同步执行的异常记录外壳；真正的启动步骤在 _run_user_turn_sync_impl。

        记录异常后重新抛出，调用方仍能处理失败或中断，不能把异常伪装成成功响应。
        """

        try:
            return self._run_user_turn_sync_impl(content, attachments=attachments)
        except BaseException as exc:
            self._persist_errored_turn(exc)
            raise

    def _run_user_turn_sync_impl(
        self,
        content: str,
        *,
        attachments: list[UserAttachment] | None = None,
    ) -> AgentTurnResult:
        """普通模式启动一轮：检查 pending -> 保存输入 -> 标记任务 -> 进入工具循环。"""

        if self.session.pending_permission_execution is not None:
            # 上一轮已经把 assistant tool_call 写进历史，但还缺一个匹配的 tool_result。
            # 这种情况下不能追加新的用户消息，否则 provider 会看到非法消息序列。
            pending = self.session.pending_permission_execution
            return AgentTurnResult(
                status=AgentTurnStatus.WAITING_FOR_USER_INPUT,
                pending_input=self.tool_executor.permission_input_request_from_pending(pending),
            )

        # 先验证输入并初始化本轮计数，再修复旧的中断记录，最后才追加新用户消息。
        # pending 权限操作已在上方提前返回，不会被这里的中断修复当作普通残留调用处理。
        self._validate_attachments(attachments)
        self._begin_turn()
        self._repair_interrupted_tool_calls_before_provider_request()
        self._check_cancelled()
        message_id = self.session.append_user_message(content, attachments=attachments)
        try:
            # user turn 不等于 task：用户可能是在继续原任务，也可能在切换任务。
            # 首次建立任务锚点；已有锚点时再交给分类器判断任务边界。
            if self._initialize_active_task_if_missing(message_id) is None:
                self._classify_task_boundary(message_id)
        except _AgentLoopLimitReached as exc:
            return self._complete_turn(self._limit_response(exc.reason))
        except AgentCancelledError:
            return self._complete_turn(self._interrupted_response())

        # 传函数而非调用结果：工具循环需要问模型时，才调用这个带恢复能力的函数。
        return self._run_tool_loop_interactive(
            self._complete_once_with_recovery,
        )

    async def resume_with_user_input(
        self,
        request_id: str,
        answer: str,
        *,
        streaming: bool = False,
    ) -> AgentTurnResult:
        """根据确认请求 ID 和用户选择恢复暂停任务，而不是重新执行原始用户问题。

        request_id 必须对应本地保存的 pending；工具名、参数和预览取自可信运行状态，
        不让 UI 重新提供一份可能被改动的执行参数。
        """

        if streaming:
            return await self._resume_with_user_input_streaming(request_id, answer)
        return await anyio.to_thread.run_sync(
            lambda: self._resume_with_user_input_sync(request_id, answer)
        )

    def _resume_with_user_input_sync(self, request_id: str, answer: str) -> AgentTurnResult:
        """用用户回答恢复一个暂停中的权限确认。

        普通 `ask_user` 第一版仍通过“下一条用户消息”继续；权限确认不能这样做，
        因为模型原始 tool_call 已经在历史里等待一个匹配的 tool_result。这里必须先
        用本地 pending 状态补齐最终 tool_result，再继续下一次 provider 调用。
        """

        try:
            return self._resume_with_user_input_sync_impl(request_id, answer)
        except BaseException as exc:
            self._persist_errored_turn(exc)
            raise

    def _resume_with_user_input_sync_impl(self, request_id: str, answer: str) -> AgentTurnResult:
        """同步恢复暂停的权限确认；补齐原 tool_call 的结果后才再次请求模型。"""
        # 权限确认延续原 turn：保留已用模型次数、工具轮数和执行证据，不当成新问题清零。
        pending = self.session.pending_permission_execution
        if pending is not None and pending.request_id == request_id:
            self._begin_turn(new_user_turn=False)
        try:
            self._check_turn_timeout()
            self._check_cancelled()
        except _AgentLoopLimitReached as exc:
            return self._complete_turn(self._limit_response(exc.reason))
        except AgentCancelledError:
            return self._complete_turn(self._interrupted_response())
        result = self._append_permission_resume_result(request_id, answer)
        # 返回 AgentTurnResult 表示无法继续（例如 ID 不匹配）；None 表示权限结果已写回，
        # 历史已可继续给模型，接下来仍需进入工具循环生成回答。
        if result is not None:
            return result
        self._repair_interrupted_tool_calls_before_provider_request()
        self._check_cancelled()
        return self._run_tool_loop_interactive(self._complete_once_with_recovery)

    async def _resume_with_user_input_streaming(self, request_id: str, answer: str) -> AgentTurnResult:
        """流式模式下恢复权限确认，并继续消费 provider stream。"""

        try:
            return await self._resume_with_user_input_streaming_impl(request_id, answer)
        except BaseException as exc:
            self._persist_errored_turn(exc)
            raise

    async def _resume_with_user_input_streaming_impl(self, request_id: str, answer: str) -> AgentTurnResult:
        """流式模式恢复权限暂停；授权工具在线程中执行，后续模型响应仍使用 stream。"""
        pending = self.session.pending_permission_execution
        if pending is not None and pending.request_id == request_id:
            self._begin_turn(new_user_turn=False)
        try:
            self._check_turn_timeout()
            self._check_cancelled()
        except _AgentLoopLimitReached as exc:
            return self._complete_turn(self._limit_response(exc.reason))
        except AgentCancelledError:
            return self._complete_turn(self._interrupted_response())
        result = await self._append_permission_resume_result_async(request_id, answer)
        if result is not None:
            return result
        self._check_cancelled()
        return await self._run_tool_loop_interactive_async(self._stream_once_with_recovery)

    async def _run_user_turn_streaming(
        self,
        content: str,
        *,
        attachments: list[UserAttachment] | None = None,
    ) -> AgentTurnResult:
        """使用 provider 内部 stream event 协议执行一轮会话。

        文本 delta 可以被上层即时展示，但工具调用仍保持原子语义：只有 stream 完成并
        返回完整 `ChatResponse.tool_calls` 后，才写入 assistant message 并执行工具。
        """

        try:
            return await self._run_user_turn_streaming_impl(content, attachments=attachments)
        except BaseException as exc:
            self._persist_errored_turn(exc)
            raise

    async def _run_user_turn_streaming_impl(
        self,
        content: str,
        *,
        attachments: list[UserAttachment] | None = None,
    ) -> AgentTurnResult:
        """流式模式的一轮启动步骤；顺序与同步实现一致，只替换分类和模型调用方式。"""
        self.last_stream_events = []
        if self.session.pending_permission_execution is not None:
            pending = self.session.pending_permission_execution
            pending_input = self.tool_executor.permission_input_request_from_pending(pending)
            return AgentTurnResult(
                status=AgentTurnStatus.WAITING_FOR_USER_INPUT,
                pending_input=pending_input,
            )

        self._validate_attachments(attachments)
        self._begin_turn()
        self._repair_interrupted_tool_calls_before_provider_request()
        self._check_cancelled()
        message_id = self.session.append_user_message(content, attachments=attachments)
        try:
            if self._initialize_active_task_if_missing(message_id) is None:
                await self._classify_task_boundary_async(message_id)
        except _AgentLoopLimitReached as exc:
            return self._complete_turn(self._limit_response(exc.reason))
        except AgentCancelledError:
            return self._complete_turn(self._interrupted_response())

        result = await self._run_tool_loop_interactive_async(
            self._stream_once_with_recovery,
        )
        return result

    def _initialize_active_task_if_missing(self, basis_message_id: str):
        """以真实用户消息 ID 建立任务锚点，并把 task_hash 写入该消息的 parts 元数据。"""
        service = TaskBoundaryService(known_message_ids=self.session.known_message_ids)
        observation = service.initialize_active_task(self.session.runtime_state, basis_message_id=basis_message_id)
        if observation is not None:
            self.session.writer.append_task_boundary_observation(observation)
            self._tag_message_parts_with_task_hash(basis_message_id, observation.active_task_hash)
        return observation

    def _classify_task_boundary(self, basis_message_id: str) -> None:
        """同步判断新用户消息是否开启了不同任务，并更新会话任务边界状态。"""
        self.task_boundary_classifier.classify(basis_message_id)

    async def _classify_task_boundary_async(self, basis_message_id: str) -> None:
        """异步判断任务边界；分类需要模型时不会阻塞当前事件循环。"""
        await self.task_boundary_classifier.classify_async(basis_message_id)

    def _tag_message_parts_with_task_hash(self, message_id: str, task_hash: str | None) -> None:
        """把任务 hash 作为元数据补到指定消息的每个 part；空 hash 或不存在的消息不处理。"""
        if not task_hash:
            return
        view = self.session.rebuild_view()
        message = next((message for message in view.messages if message.id == message_id), None)
        if message is None:
            return
        for part in message.parts:
            self.session.writer.append_message_part_metadata_updated(
                message_id=message_id,
                part_id=part.id,
                metadata={"task_hash": task_hash},
            )

    def _tag_task_boundary_messages_with_active_hash(self, data: dict[str, object]) -> None:
        """读取 task_boundary 工具返回的消息锚点，并标记当前任务涉及的原始消息。"""
        active_hash = data.get("active_task_hash")
        message_ids = {
            str(data.get("basis_message_id") or ""),
            str(data.get("candidate_basis_message_id") or ""),
        }
        for message_id in message_ids:
            if message_id:
                self._tag_message_parts_with_task_hash(message_id, active_hash)

    def _append_permission_resume_result(self, request_id: str, answer: str) -> AgentTurnResult | None:
        """补齐 pending 的最终工具结果；拒绝也要写成结果，使调用与结果保持配对。"""
        pending = self._pending_permission_for_resume(request_id)
        if isinstance(pending, AgentTurnResult):
            return pending
        result = self._prepare_permission_resume(pending, answer)
        # None 在这里表示“允许进入执行”，不是“没有工具结果”；执行后再统一收尾。
        if result is None:
            result = self._execute_resumed_permission_tool_call(pending)
            self._emit_finished_permission_resume(pending, result)
        self._finish_permission_resume(pending, result)
        return None

    async def _append_permission_resume_result_async(self, request_id: str, answer: str) -> AgentTurnResult | None:
        """异步恢复版权限收尾；同步工具执行移入线程，事件仍在 loop 所在线程处理。"""
        pending = self._pending_permission_for_resume(request_id)
        if isinstance(pending, AgentTurnResult):
            return pending
        result = self._prepare_permission_resume(pending, answer)
        if result is None:
            result = await anyio.to_thread.run_sync(self._execute_resumed_permission_tool_call, pending)
            self._emit_finished_permission_resume(pending, result)
        self._finish_permission_resume(pending, result)
        return None

    def _pending_permission_for_resume(
        self,
        request_id: str,
    ) -> PendingPermissionExecution | AgentTurnResult:
        """按 request_id 找本地保存的 pending；无匹配项时返回错误结果供调用方直接结束。"""
        pending = self.session.pending_permission_execution
        if pending is None or pending.request_id != request_id:
            return AgentTurnResult(
                status=AgentTurnStatus.COMPLETED,
                response=ChatResponse(
                    provider=self.provider.name,
                    model=self.provider.model,
                    content="没有找到可恢复的权限确认请求。",
                    finish_reason="error",
                ),
            )
        if self.session.permission_manager is None:
            return AgentTurnResult(
                status=AgentTurnStatus.COMPLETED,
                response=ChatResponse(
                    provider=self.provider.name,
                    model=self.provider.model,
                    content="当前会话没有权限管理器，无法恢复权限确认。",
                    finish_reason="error",
                ),
            )
        return pending

    def _prepare_permission_resume(
        self,
        pending: PendingPermissionExecution,
        answer: str,
    ) -> ToolResult | None:
        """校验用户选择、确认状态与写前预览；返回失败结果表示禁止执行，None 表示可执行。"""
        result = self._blocked_permission_resume_result(pending, answer)
        if result is not None:
            self._emit_tool_event(
                "denied",
                pending.tool_call,
                result=result,
                permission_request=pending.permission_request,
            )
            return result
        self._emit_tool_event(
            "started",
            pending.tool_call,
            permission_request=pending.permission_request,
        )
        self._check_cancelled()
        return None

    def _execute_resumed_permission_tool_call(self, pending: PendingPermissionExecution) -> ToolResult:
        """执行保存在 pending 中的原始调用；不接受 UI 重传工具名或参数。"""
        # 用户同意后使用 session 保存的原始 tool_call，不能相信 UI 回传的参数。
        return self.tool_executor.execute_after_permission_with_cancellation_context(pending.tool_call)

    def _emit_finished_permission_resume(
        self,
        pending: PendingPermissionExecution,
        result: ToolResult,
    ) -> None:
        """向 UI/上层报告已确认工具的最终结果，与暂停前的 permission_requested 事件配对。"""
        self._emit_tool_event(
            "finished",
            pending.tool_call,
            result=result,
            permission_request=pending.permission_request,
        )

    def _finish_permission_resume(self, pending: PendingPermissionExecution, result: ToolResult) -> None:
        """完成授权恢复的持久化收尾，并结算同批被跳过的调用。"""
        # 本批工具请求已在暂停前保存，不再追加一遍 assistant tool_call。
        # 只补本次结果，并给同批被跳过的调用补跳过记录，然后计为完成一轮。
        self.session.pending_permission_execution = None
        self._observe_tool_result(pending.tool_call, result)
        self.session.append_tool_result(tool_call=pending.tool_call, result=result)
        self._emit_settlements("skipped", self.tool_settlement.append_skipped(pending.skipped_tool_calls))
        self._tool_rounds_completed += 1

    def _resolve_pending_confirmation(
        self,
        pending: PendingPermissionExecution,
        answer: str,
    ):
        """把用户文本选择解析成权限决定；写前预览只允许本次应用，不创建长期授权。"""
        if not pending.review_only:
            return self.session.permission_manager.resolve_confirmation(pending.permission_request, answer)
        normalized = answer.strip().lower()
        if normalized in {"allow_once", "allow", "once", "2"}:
            current = self.session.preflight_tool_call_permission(pending.tool_call)
            if current is not None and current.decision.kind == PermissionDecisionKind.DENY:
                return current.decision
            return PermissionDecision(kind=PermissionDecisionKind.ALLOW, reason="用户批准应用已预览的修改。")
        if normalized in {"deny", "no", "1"} or normalized.startswith(("reject:", "reject_with_feedback:")):
            return self.session.permission_manager.resolve_confirmation(pending.permission_request, answer)
        return PermissionDecision(
            kind=PermissionDecisionKind.DENY,
            reason=f"未知写前预览选择：{answer}",
        )

    def _blocked_permission_resume_result(
        self,
        pending: PendingPermissionExecution,
        answer: str,
    ) -> ToolResult | None:
        """执行前再次检查授权与写前预览；None 表示通过，否则返回拒绝/过期等失败结果。"""
        decision = self._resolve_pending_confirmation(pending, answer)
        if decision.kind == PermissionDecisionKind.DENY:
            return make_permission_denied_result(
                tool_name=pending.tool_call.name,
                request=pending.permission_request,
                decision=decision,
            )
        if pending.prewrite_review is None:
            return None
        if not pending.prewrite_review.ok:
            return make_prewrite_review_failed_result(
                tool_name=pending.tool_call.name,
                request=pending.permission_request,
                error=pending.prewrite_review.error or "未知错误",
            )
        # 用户确认期间文件可能被其他进程修改；原预览过期时不能直接套用旧修改。
        if pending.prewrite_review.is_current(
            self.session.permission_manager.policy.project_root,
            access=self.session.sandbox_access,
        ):
            return None
        return make_prewrite_review_stale_result(
            tool_name=pending.tool_call.name,
            request=pending.permission_request,
        )

    def _complete_once(
        self,
        *,
        tool_choice="auto",
        runtime_instruction: str | None = None,
    ) -> ChatResponse:
        """构造一次 provider 请求并获得模型响应。

        这一步只负责“问模型一次”，不处理工具循环。拆开后，同步调用、streaming 调用、
        prompt-too-long 恢复都可以复用同一套上下文构造逻辑。

        prepared.request 是模型要看的消息和工具；prepared 的其余字段用于记录本次投影。
        响应在这里尚未写成 assistant 历史：有工具的响应由工具循环写，最终响应由
        _complete_turn 写，避免同一响应保存两次。
        """

        prepared = self._prepare_main_provider_request(
            tool_choice=tool_choice,
            runtime_instruction=runtime_instruction,
        )
        # 发请求前占用一次调用预算，所以失败的 API 尝试也会计数。
        self._reserve_provider_call()
        self._check_turn_timeout()
        self._check_cancelled()
        response = self.provider.complete(prepared.request)
        # 只有成功收到响应，才记录本次请求消费了哪些工具结果；失败不能当成已消费。
        self._record_projection_consumed(prepared)
        return response

    def _main_chat_request(self, messages, definitions, tool_choice) -> ChatRequest:
        """将已投影的消息、可见工具和本轮模型参数组装成统一 ChatRequest。"""
        return ChatRequest(
            messages=messages,
            tools=definitions,
            tool_choice=tool_choice,
            **self.request_options.as_chat_request_kwargs(),
        )

    def _complete_once_with_recovery(
        self,
        *,
        tool_choice="auto",
        runtime_instruction: str | None = None,
    ) -> ChatResponse:
        """给一次普通模型调用加恢复策略：瞬时失败有限重试，上下文过长先尝试压缩。

        provider 如果拒绝请求，说明 assistant 回复还没有产生，也就没有新消息要落库。
        这时可以先触发 blocking compact，再重建 provider messages 重试一次。
        两类恢复分别计数：transient_retries 限制普通重试，compaction_attempted 防止
        同一次恢复流程反复压缩。认证等不能恢复的错误以及用完预算的错误继续向上抛。
        """

        transient_retries = 0
        compaction_attempted = False
        while True:
            try:
                return self._complete_once(
                    tool_choice=tool_choice,
                    runtime_instruction=runtime_instruction,
                )
            except ProviderError as exc:
                if exc.retryable and transient_retries < self.provider_retry_policy.max_retries:
                    transient_retries += 1
                    self.turn_telemetry.observe_provider_retry(exc.kind)
                    self._wait_before_provider_retry(transient_retries)
                    continue
                if exc.requires_compaction and not compaction_attempted:
                    # 这不是原样重发超长请求：context 层先整理历史，下一次 _complete_once
                    # 会重新构造消息。没有 manager 或压缩未成功时，原错误仍会抛出。
                    compaction_attempted = True
                    result = self._compact_for_prompt_too_long(
                        runtime_instruction=runtime_instruction
                    )
                    if result is not None and result.status == "success":
                        self.turn_telemetry.observe_provider_retry(exc.kind)
                        continue
                raise

    async def _stream_once(
        self,
        *,
        tool_choice="auto",
        runtime_instruction: str | None = None,
    ) -> ChatResponse:
        """消费一次 provider stream，最终仍返回完整 ChatResponse。

        UI 可以读取 `last_stream_events` 展示 text_delta；但工具调用必须等 stream 完成后
        才能执行，因为 OpenAI-compatible 的 tool arguments 可能分散在多个 chunk 中。
        """

        prepared = self._prepare_main_provider_request(
            tool_choice=tool_choice,
            runtime_instruction=runtime_instruction,
        )
        # step10 中 parts/completed 的真实对应：provider 累积片段；loop 接收事件用于
        # 展示，最终仍把完整 ChatResponse 交给同样的工具循环。
        final_response: ChatResponse | None = None
        self._reserve_provider_call()
        self._check_turn_timeout()
        self._check_cancelled()
        async for event in self.provider.astream(prepared.request):
            self._check_cancelled()
            self.last_stream_events.append(event)
            if self.stream_event_handler is not None:
                self.stream_event_handler(event)
            if event.kind == "message_completed":
                final_response = event.response
        # 流结束不代表成功完成；没收到最终响应就不能把局部文本当作完整回答。
        if final_response is None:
            raise ProviderError(
                ProviderErrorKind.API_ERROR,
                "provider stream ended without message_completed event",
            )
        self._record_projection_consumed(prepared)
        return final_response

    async def _stream_once_with_recovery(
        self,
        *,
        tool_choice="auto",
        runtime_instruction: str | None = None,
    ) -> ChatResponse:
        """恢复流式请求；瞬时失败按策略等待，并在分支规定的次数后退回普通调用。

        退回同步调用仍经过 _complete_once_with_recovery，有自己的普通重试流程；
        所有实际请求还受本轮总调用次数、耗时和取消检查约束。
        """
        transient_retries = 0
        compaction_attempted = False
        while True:
            try:
                return await self._stream_once_attempt(
                    tool_choice=tool_choice,
                    runtime_instruction=runtime_instruction,
                )
            except ProviderError as exc:
                if exc.retryable:
                    transient_retries += 1
                    if transient_retries <= self.provider_retry_policy.max_retries:
                        self.turn_telemetry.observe_provider_retry(exc.kind)
                        await self._wait_before_provider_retry_async(transient_retries)
                    if transient_retries < self.provider_retry_policy.max_retries:
                        continue
                    return self._complete_once_with_recovery(
                        tool_choice=tool_choice,
                        runtime_instruction=runtime_instruction,
                    )
                if exc.requires_compaction and not compaction_attempted:
                    compaction_attempted = True
                    result = self._compact_for_prompt_too_long(
                        runtime_instruction=runtime_instruction
                    )
                    if result is not None and result.status == "success":
                        self.turn_telemetry.observe_provider_retry(exc.kind)
                        continue
                raise

    def _wait_before_provider_retry(self, retry_number: int) -> None:
        """按退避策略等待一次重试，并在等待前后检查时间预算与取消状态。"""
        # 等待前后都检查预算和取消。这里不是硬中断正在进行的网络 I/O；底层调用自身
        # 仍需超时设置，取消也需要执行中的工具配合。
        self._check_turn_timeout()
        self._check_cancelled()
        delay = self.provider_retry_policy.delay_for_retry(retry_number)
        self.provider_retry_sleeper(delay)
        self._check_turn_timeout()
        self._check_cancelled()

    async def _wait_before_provider_retry_async(self, retry_number: int) -> None:
        """异步调用重试等待器，并在等待前后检查本轮时间预算和取消信号。"""
        self._check_turn_timeout()
        self._check_cancelled()
        delay = self.provider_retry_policy.delay_for_retry(retry_number)
        await anyio.to_thread.run_sync(self.provider_retry_sleeper, delay)
        self._check_turn_timeout()
        self._check_cancelled()

    async def _stream_once_attempt(
        self,
        *,
        tool_choice="auto",
        runtime_instruction: str | None = None,
    ) -> ChatResponse:
        """包住单次 stream 尝试；失败时删除本次未完成的局部事件，防止 UI 拼出假答案。"""
        start_event_count = len(self.last_stream_events)
        try:
            return await self._stream_once(
                tool_choice=tool_choice,
                runtime_instruction=runtime_instruction,
            )
        except ProviderError:
            # streaming 尝试失败时，不能把已经收到的局部 delta 当成真实回答留给 UI。
            # 真正成功的重试会重新产生完整事件。
            del self.last_stream_events[start_event_count:]
            raise

    def _run_tool_loop_interactive(self, complete_once, *, initial_tool_choice="auto") -> AgentTurnResult:
        """核心工具循环：问模型，执行工具，再把工具结果回喂给模型。

        complete_once 是调用方传入的函数，不是 ChatResponse；本方法可反复调用它。
        普通模式传 _complete_once_with_recovery；流式模式走下方的异步对应实现。

        先问模型一次，再把有工具的响应交给 _continue_tool_loop_from_response。
        没有工具时得到的是候选结束响应（也可能是截断或错误），尚需按配置对齐未完成
        TaskPlan、检查执行证据，然后才落盘收尾；这些检查不是所有运行模式都启用。
        工具需要用户输入时返回 pending；预算耗尽或取消时则构造停止响应。
        """

        guardrail_stop = False
        try:
            if self.max_tool_rounds is not None and self._tool_rounds_completed >= self.max_tool_rounds:
                return self._complete_turn(self._limit_response(AgentLoopStopReason.TOOL_ROUND_LIMIT))
            response = self._drop_unsupported_tool_calls(complete_once(tool_choice=initial_tool_choice))
            tool_rounds = self._tool_rounds_completed
            response, pending_input, tool_rounds = self._continue_tool_loop_from_response(
                response,
                complete_once,
                tool_rounds,
            )
            if pending_input is not None:
                return self._pending_turn_result(pending_input)
            # “模型不再调工具”不必然等于任务已完成：先按需提醒它对齐计划和验证证据。
            # 收尾提示可能引发新工具调用，因此仍传入当前轮次，不能另开无预算的新循环。
            if response.finish_reason != AgentLoopStopReason.TOOL_ROUND_LIMIT.value:
                response, pending_input, tool_rounds = self._run_task_plan_reconciliation_if_needed(
                    response,
                    complete_once,
                    tool_rounds,
                )
                if pending_input is not None:
                    return self._pending_turn_result(pending_input)
                response, pending_input, _ = self._run_completion_gate_if_needed(
                    response,
                    complete_once,
                    tool_rounds,
                )
                if pending_input is not None:
                    return self._pending_turn_result(pending_input)
        except _AgentLoopLimitReached as exc:
            response = self._limit_response(exc.reason)
            guardrail_stop = True
        except AgentCancelledError:
            # 不把未闭合调用留给下一次 provider 请求，也不声称被中断的操作必然未执行。
            self._append_interrupted_tool_results()
            response = self._interrupted_response()

        if self._is_cancelled():
            self._append_interrupted_tool_results()
            response = self._interrupted_response()
            return self._complete_turn(response)
        if guardrail_stop:
            return self._complete_turn(response)

        # 没有工具调用时，这条 response 就是最终 assistant 回复。命中轮次上限时也会写入
        # 一条纯文本说明，避免保存未执行的 tool_call。
        return self._complete_turn(response)

    async def _run_tool_loop_interactive_async(self, complete_once, *, initial_tool_choice="auto") -> AgentTurnResult:
        """流式路径的对应协调循环；await 接收完整模型响应后才开始执行工具。

        对照上方同步版本阅读：暂停、预算、计划对齐、完成检查和最终保存的语义相同。
        流式文本展示由 _stream_once 的事件回调完成，不改变 tool_call/result 配对规则。
        """

        guardrail_stop = False
        try:
            if self.max_tool_rounds is not None and self._tool_rounds_completed >= self.max_tool_rounds:
                return self._complete_turn(self._limit_response(AgentLoopStopReason.TOOL_ROUND_LIMIT))
            response = self._drop_unsupported_tool_calls(await complete_once(tool_choice=initial_tool_choice))
            tool_rounds = self._tool_rounds_completed
            response, pending_input, tool_rounds = await self._continue_tool_loop_from_response_async(
                response,
                complete_once,
                tool_rounds,
            )
            if pending_input is not None:
                return self._pending_turn_result(pending_input)
            if response.finish_reason != AgentLoopStopReason.TOOL_ROUND_LIMIT.value:
                response, pending_input, tool_rounds = await self._run_task_plan_reconciliation_if_needed_async(
                    response,
                    complete_once,
                    tool_rounds,
                )
                if pending_input is not None:
                    return self._pending_turn_result(pending_input)
                response, pending_input, _ = await self._run_completion_gate_if_needed_async(
                    response,
                    complete_once,
                    tool_rounds,
                )
                if pending_input is not None:
                    return self._pending_turn_result(pending_input)
        except _AgentLoopLimitReached as exc:
            response = self._limit_response(exc.reason)
            guardrail_stop = True
        except AgentCancelledError:
            self._append_interrupted_tool_results()
            response = self._interrupted_response()

        if self._is_cancelled():
            self._append_interrupted_tool_results()
            response = self._interrupted_response()
            return self._complete_turn(response)
        if guardrail_stop:
            return self._complete_turn(response)

        return self._complete_turn(response)

    def _pending_turn_result(self, pending_input: UserInputRequest) -> AgentTurnResult:
        """把权限确认或 ask_user 请求包装为可暂停结果，并保留未结束的遥测状态。"""
        # paused 不是完成：不在这里写最终 assistant 回复，也不结束本轮遥测，之后可恢复。
        self._persist_turn_telemetry(
            status="paused",
            stop_reason=pending_input.kind,
            finalize=False,
        )
        return AgentTurnResult(status=AgentTurnStatus.WAITING_FOR_USER_INPUT, pending_input=pending_input)

    def _complete_turn(self, response: ChatResponse) -> AgentTurnResult:
        """保存结束响应与遥测，并返回给调用方。

        AgentTurnStatus.COMPLETED 表示本次处理已结束、当前无需等待用户，不保证业务成功。
        具体是正常、超限、截断还是错误，要看 response.finish_reason 和遥测状态。
        """
        self.session.append_assistant_response(response)
        status = "completed"
        if response.finish_reason == "interrupted":
            status = "interrupted"
        elif response.finish_reason == "length" or response.finish_reason in {
            reason.value for reason in AgentLoopStopReason
        }:
            status = "limited"
        elif response.finish_reason in {"error", "content_filter"}:
            status = "errored"
        self._persist_turn_telemetry(
            status=status,
            stop_reason=response.finish_reason or "completed",
            finalize=True,
        )
        return AgentTurnResult(status=AgentTurnStatus.COMPLETED, response=response)

    def _continue_tool_loop_from_response(
        self,
        response: ChatResponse,
        complete_once,
        tool_rounds: int,
    ) -> tuple[ChatResponse, UserInputRequest | None, int]:
        """核心反馈循环；与教学版 step02 的 while/工具执行部分直接对应。

        输入 response 是已经取得的模型响应；返回 (最后的响应, 待用户处理的请求或 None,
        已完成工具轮数)。有 pending 时尚未形成最终回答，调用方必须先走暂停流程。

        例：响应 1 请求 add(2, 3) -> 记录请求 -> 执行并记录 5 -> complete_once()
        发出包含结果 5 的新请求；响应 2 若无 tool_calls，就退出 while 交给外层收尾。
        """
        while response.tool_calls:
            self._check_cancelled()
            if self.max_tool_rounds is not None and tool_rounds >= self.max_tool_rounds:
                return self._tool_round_limit_response(response), None, tool_rounds

            # 关键顺序：必须先写 assistant tool_call，再写对应 tool_result。provider 后续
            # 才能看到合法的 “assistant(tool_calls) -> tool(result)” 消息序列。
            # response.content 若有公开说明也会一起保存；不是只保存工具名和参数。
            self.session.append_assistant_response(response)
            # ToolExecutor 内部负责权限、执行/调度、观察回调和结果持久化，不要在这里
            # 再追加一次 tool_result。批次可能含多个工具，也可能在权限确认处暂停。
            execution = self.tool_executor.execute_interactive(response.tool_calls)
            if execution.pending_input is not None:
                # 暂停时不能再问模型：原始 tool_call 仍在等一个最终结果，先交还 UI。
                return response, execution.pending_input, tool_rounds
            if execution.task_hash_changed:
                self._compact_after_task_hash_changed()

            # 一个响应中即使有两个工具，也只算一轮；provider 调用次数另行计数。
            tool_rounds += 1
            self._tool_rounds_completed = tool_rounds
            if self.max_tool_rounds is not None and tool_rounds >= self.max_tool_rounds:
                # 边界语义：执行完第 N 轮即停止，不额外请求模型生成总结。
                return self._tool_round_limit_response(response), None, tool_rounds
            self._check_cancelled()
            # 真正的“观察 -> 下一步”：重建请求时会带上刚写入历史的工具结果。
            # 下一步由新模型响应决定，loop 并没有写死 add 之后一定是 multiply。
            response = self._drop_unsupported_tool_calls(complete_once())
        return response, None, tool_rounds

    def _run_task_plan_reconciliation_if_needed(
        self,
        response: ChatResponse,
        complete_once,
        tool_rounds: int,
    ) -> tuple[ChatResponse, UserInputRequest | None, int]:
        """若 TaskPlan 仍有未完成项，给模型一次收尾指令，并继续处理它返回的工具调用。"""
        instruction = self._final_reconciliation_instruction()
        if instruction is None:
            return response, None, tool_rounds
        response = self._drop_unsupported_tool_calls(complete_once(runtime_instruction=instruction))
        return self._continue_tool_loop_from_response(response, complete_once, tool_rounds)

    async def _run_task_plan_reconciliation_if_needed_async(
        self,
        response: ChatResponse,
        complete_once,
        tool_rounds: int,
    ) -> tuple[ChatResponse, UserInputRequest | None, int]:
        """任务计划收尾检查的异步版本；保持与同步分支相同的预算和暂停语义。"""
        instruction = self._final_reconciliation_instruction()
        if instruction is None:
            return response, None, tool_rounds
        response = self._drop_unsupported_tool_calls(await complete_once(runtime_instruction=instruction))
        return await self._continue_tool_loop_from_response_async(response, complete_once, tool_rounds)

    def _final_reconciliation_instruction(self) -> str | None:
        """至多安排一次计划对齐提示；它是下一次请求的临时上下文，不是用户新输入。"""
        if self._task_plan_reconciliation_attempted:
            return None
        instruction = self.task_plan_policy.final_reconciliation_instruction()
        if instruction is None:
            return None
        self._task_plan_reconciliation_attempted = True
        return instruction

    def _run_completion_gate_if_needed(
        self,
        response: ChatResponse,
        complete_once,
        tool_rounds: int,
    ) -> tuple[ChatResponse, UserInputRequest | None, int]:
        """根据执行证据决定是否要求模型继续验证；新指令也可能触发工具调用或暂停。"""
        while True:
            instruction = self._completion_gate_instruction()
            if instruction is None:
                return response, None, tool_rounds
            response = self._drop_unsupported_tool_calls(
                complete_once(runtime_instruction=instruction)
            )
            response, pending_input, tool_rounds = self._continue_tool_loop_from_response(
                response,
                complete_once,
                tool_rounds,
            )
            if pending_input is not None:
                return response, pending_input, tool_rounds

    async def _run_completion_gate_if_needed_async(
        self,
        response: ChatResponse,
        complete_once,
        tool_rounds: int,
    ) -> tuple[ChatResponse, UserInputRequest | None, int]:
        """完成检查的异步版本；由能力配置控制启用，并受最大尝试数限制。"""
        while True:
            instruction = self._completion_gate_instruction()
            if instruction is None:
                return response, None, tool_rounds
            response = self._drop_unsupported_tool_calls(
                await complete_once(runtime_instruction=instruction)
            )
            response, pending_input, tool_rounds = await self._continue_tool_loop_from_response_async(
                response,
                complete_once,
                tool_rounds,
            )
            if pending_input is not None:
                return response, pending_input, tool_rounds

    def _completion_gate_instruction(self) -> str | None:
        """启用时检查执行证据和后台任务，生成继续验证的提示，并限制追问次数。

        这不是无限驻留的监管器；外层仍受工具轮次、调用次数和时间预算约束。
        """
        if (
            not self.runtime_capabilities.enable_completion_gate
            or self._completion_gate_attempted
            or self._completion_gate_attempt_count >= MAX_COMPLETION_GATE_ATTEMPTS
        ):
            return None
        jobs = ()
        if self.background_manager is not None:
            jobs = self.background_manager.list(session_id=self.session.session_id)
        decision = self.execution_evidence.completion_decision(
            background_jobs=jobs,
        )
        instruction = decision.render_instruction()
        if instruction is not None:
            self._completion_gate_attempted = True
            self._completion_gate_attempt_count += 1
            self.turn_telemetry.observe_completion_gate(reason_count=len(decision.reasons))
        return instruction

    async def _continue_tool_loop_from_response_async(
        self,
        response: ChatResponse,
        complete_once,
        tool_rounds: int,
    ) -> tuple[ChatResponse, UserInputRequest | None, int]:
        """上方反馈循环的异步版本；差别在于等待模型和工具执行使用 await。

        流式工具参数已经由 provider 收集完整，这里不对半截 arguments 执行工具。
        """
        while response.tool_calls:
            self._check_cancelled()
            if self.max_tool_rounds is not None and tool_rounds >= self.max_tool_rounds:
                return self._tool_round_limit_response(response), None, tool_rounds

            self.session.append_assistant_response(response)
            execution = await self.tool_executor.execute_interactive_async(response.tool_calls)
            if execution.pending_input is not None:
                return response, execution.pending_input, tool_rounds
            if execution.task_hash_changed:
                self._compact_after_task_hash_changed()

            tool_rounds += 1
            self._tool_rounds_completed = tool_rounds
            if self.max_tool_rounds is not None and tool_rounds >= self.max_tool_rounds:
                return self._tool_round_limit_response(response), None, tool_rounds
            self._check_cancelled()
            response = self._drop_unsupported_tool_calls(await complete_once())
        return response, None, tool_rounds

    def _append_interrupted_tool_results(self) -> None:
        """为历史尾部尚未闭合的调用追加“执行被中断、结果未知”结果，并通知事件监听器。"""
        self._emit_settlements("interrupted", self.tool_settlement.append_interrupted_tail())

    def _repair_interrupted_tool_calls_before_provider_request(self) -> None:
        """发新请求前修复未闭合的历史尾部，避免 provider 收到孤立 tool_call。"""
        # 对没有 pending 权限状态的残留调用补“执行结果未知”的中断结果；这只修复历史
        # 的协议配对，不会重跑工具，也不会回滚已经产生的副作用。
        self._emit_settlements("interrupted", self.tool_settlement.repair_before_provider_request())

    def _emit_settlements(self, kind, settlements) -> None:
        """把 settlement helper 生成的已闭合结果逐个转换成 UI 可消费的工具事件。"""
        for settlement in settlements:
            self._emit_tool_event(kind, settlement.tool_call, result=settlement.result)

    def _emit_tool_event(
        self,
        kind: Literal[
            "prewrite_review",
            "started",
            "finished",
            "permission_requested",
            "denied",
            "skipped",
            "interrupted",
            "background_started",
        ],
        tool_call: ToolCall,
        *,
        result: ToolResult | None = None,
        permission_request: PermissionRequest | None = None,
        prewrite_review: dict[str, object] | None = None,
    ) -> None:
        """将一次工具生命周期变化发给可选监听器；未安装监听器时只影响展示，不影响执行。"""
        if self.tool_event_handler is None:
            return
        self.tool_event_handler(
            ToolExecutionEvent(
                kind=kind,
                tool_call=tool_call,
                result=result,
                permission_request=permission_request,
                prewrite_review=prewrite_review,
            )
        )

    def _prepare_main_provider_request(
        self,
        *,
        tool_choice="auto",
        runtime_instruction: str | None = None,
    ) -> PreparedMainRequest:
        """构造这一次模型调用的完整输入；对应 step12 的“会话事实 -> 请求投影”。

        顺序：修复调用配对 -> 接收追加指导与后台结果 -> 选择可见工具 -> 重建视图
        -> 估算预算并按需压缩 -> 再投影 -> 封装 ChatRequest 与请求追踪信息。
        不直接把 JSONL 原样交给模型，也不在这里发网络请求。
        """
        self._repair_interrupted_tool_calls_before_provider_request()
        self._check_cancelled()
        self._append_pending_guidance()
        self._append_background_notifications()
        definitions = self._provider_tool_definitions()
        view = self.session.rebuild_view()
        budget = self._context_budget_for_view(
            view,
            runtime_instruction=runtime_instruction,
            definitions=definitions,
        )
        if self.context_manager is not None:
            result = self.context_manager.compact_if_needed(
                ContextCompactRequest(
                    view=view,
                    runtime_state=self.session.runtime_state,
                    budget=budget,
                    estimate_budget=lambda candidate: self._context_budget_for_view(
                        candidate,
                        runtime_instruction=runtime_instruction,
                        definitions=definitions,
                    ),
                    trigger=ContextWindowTrigger.AUTO,
                    current_turn=self.session.current_turn,
                )
            )
            if result.status == "success":
                # 压缩可能写入新事件；必须重建视图，不能继续使用压缩前的消息快照。
                view = self.session.rebuild_view()

        messages = self._request_messages(
            view=view,
            runtime_instruction=runtime_instruction,
        )
        request = self._main_chat_request(messages, definitions, tool_choice)
        return PreparedMainRequest(
            request=request,
            request_id=new_request_id(),
            projection_fingerprint=stable_json_hash(
                {
                    "messages": [asdict(message) for message in messages],
                    "tools": [asdict(definition) for definition in definitions],
                },
                length=24,
            ),
            tool_result_part_ids=self.context_builder.projected_tool_result_part_ids(view),
        )

    def _record_projection_consumed(self, prepared: PreparedMainRequest) -> None:
        """记录成功请求消费过的工具结果 part，供后续上下文整理使用；不等于任务已成功。"""
        self.session.record_provider_projection_consumed(
            request_id=prepared.request_id,
            projection_fingerprint=prepared.projection_fingerprint,
            part_ids=prepared.tool_result_part_ids,
            provider=self.provider.name,
            model=self.provider.model,
        )

    def _context_budget_for_view(
        self,
        view,
        *,
        runtime_instruction: str | None,
        definitions,
    ) -> ContextBudget:
        """按当前可见消息、工具 schema、上下文窗口和输出预留估算请求预算。"""
        # 估算必须包含 system prompt、工具 schema 和输出预留；只数用户文本会低估。
        messages = self._request_messages(
            view=view,
            runtime_instruction=runtime_instruction,
        )
        return build_context_budget(
            messages=messages,
            tools=definitions,
            context_window=self.context_window,
            max_output_tokens=self.request_options.max_tokens,
        )

    def context_budget_for_view(self, view) -> ContextBudget:
        """公开给上层 UI/检查器的预算估算入口，不发送请求也不修改会话。"""
        return self._context_budget_for_view(
            view,
            runtime_instruction=None,
            definitions=self._provider_tool_definitions(),
        )

    def _compact_if_needed(
        self,
        *,
        trigger: ContextWindowTrigger,
        runtime_instruction: str | None = None,
    ):
        """把压缩触发交给 context manager。

        AgentLoop 不判断 token 细节，也不决定 L1/L2/L3/L4 怎么做；它只在关键时机告诉
        context 层：“现在可能需要整理上下文了”。
        """

        if self.context_manager is None:
            return None
        definitions = self._provider_tool_definitions()
        view = self.session.rebuild_view()
        budget = self._context_budget_for_view(
            view,
            runtime_instruction=runtime_instruction,
            definitions=definitions,
        )
        return self.context_manager.compact_if_needed(
            ContextCompactRequest(
                view=view,
                runtime_state=self.session.runtime_state,
                budget=budget,
                estimate_budget=lambda candidate: self._context_budget_for_view(
                    candidate,
                    runtime_instruction=runtime_instruction,
                    definitions=definitions,
                ),
                trigger=trigger,
                current_turn=self.session.current_turn,
            )
        )

    def _compact_for_prompt_too_long(self, *, runtime_instruction: str | None = None):
        """provider 明确报告 prompt 超长时，请 context manager 按该原因尝试压缩一次。"""
        return self._compact_if_needed(
            trigger=ContextWindowTrigger.PROMPT_TOO_LONG,
            runtime_instruction=runtime_instruction,
        )

    def _compact_after_task_hash_changed(self):
        """任务边界变化后触发上下文整理，避免把上一任务的大量细节带入新任务。"""
        return self._compact_if_needed(trigger=ContextWindowTrigger.TASK_HASH_CHANGED)

    def _build_provider_messages(self, view, *, system_prefix):
        """委托 ContextBuilder 把内部 SessionView 投影为 provider 消息，并传附件存储根目录。"""
        return self.context_builder.build_provider_messages(
            view,
            system_prefix=system_prefix,
            store_root=self.session.store.root,
        )

    def _request_messages(self, *, view=None, runtime_instruction: str | None = None):
        """组合请求专用的 system 前缀，再让 ContextBuilder 投影会话历史。

        runtime_instruction、验收要求与最新任务计划只加入这次请求，不因此追加用户消息。
        真正的用户/工具事实仍保存在 session；每次请求都可以据最新视图重新构造。
        """
        resolved_view = view or self.session.rebuild_view()
        system_prefix = self.session.build_system_prefix(
            provider_name=self.provider.name,
            provider_model=self.provider.model,
            provider_capabilities=getattr(self.provider, "capabilities", None),
        )
        if runtime_instruction:
            system_prefix = [
                *system_prefix,
                ChatMessage(role="system", content=runtime_instruction),
            ]
        acceptance_contract = self.execution_evidence.render_acceptance_contract()
        if acceptance_contract:
            system_prefix = [
                *system_prefix,
                ChatMessage(role="system", content=acceptance_contract),
            ]
        if resolved_view.task_plan is not None:
            system_prefix = [
                *system_prefix,
                ChatMessage(
                    role="system",
                    content=render_current_task_plan_snapshot(resolved_view.task_plan),
                ),
            ]
        return self._build_provider_messages(
            resolved_view,
            system_prefix=system_prefix,
        )

    def _provider_tool_definitions(self):
        """从注册表中筛选本轮模型可见的工具 schema，而不是直接暴露全部 executor。

        过滤 provider 不支持的工具、内部控制工具、尚未激活的 MCP 工具和运行模式禁用项。
        可见性只是请求侧筛选；执行侧仍由 _validate_tool_call 等入口复核。
        """

        capabilities = getattr(self.provider, "capabilities", None)
        if capabilities is not None and not capabilities.supports_tools:
            return []
        definitions = []
        for definition in self.session.tool_registry.definitions():
            if definition.name in HIDDEN_TOOL_STATUS_NAMES:
                continue
            if (
                definition.name in self._mcp_tool_names
                and definition.name not in self._active_mcp_tool_names
            ):
                continue
            if definition.name == "think" and not self.runtime_capabilities.expose_think_tool:
                continue
            if (
                definition.name == "web_search"
                and not self.runtime_capabilities.expose_web_search_tool
            ):
                continue
            if (
                definition.name == "ask_user"
                and not self.runtime_capabilities.allow_user_input
            ):
                continue
            if (
                definition.name in PLANNING_TOOL_NAMES
                and not self.runtime_capabilities.expose_planning_tools
                and self.session.rebuild_view().task_plan is None
            ):
                continue
            definitions.append(self._augment_tool_definition(definition))
        return definitions

    def _augment_tool_definition(self, definition):
        """给后台可用工具的 schema 附加 run_in_background/background_label 控制字段。

        只有在启用了 background_manager 且工具在允许列表里时才增强，避免向模型暴露它
        无法真正使用的控制字段。
        """

        if self.background_manager is None:
            return definition
        if definition.name not in self.background_tool_names:
            return definition
        return with_background_controls(definition)

    def _ensure_background_control_tools(self) -> None:
        """后台运行时存在时，注册查询/取消工具；重复调用不会重复注册同名工具。"""

        if self.background_manager is None:
            return
        names = set(self.session.tool_registry.names())
        if "background_status" not in names:
            self.session.tool_registry.register(create_background_status_tool(self.background_manager, session_id=self.session.session_id))
        if "background_cancel" not in names:
            self.session.tool_registry.register(create_background_cancel_tool(self.background_manager, session_id=self.session.session_id))

    def _ensure_delegate_tool(self) -> None:
        """给父模型注册 delegate，让“启动一个子 Agent”也通过工具协议进入循环。

        子 Agent 使用自己的会话和角色工具范围；runner 复用当前 provider，明确禁用
        子 Agent 再次 delegate。这里只装配能力，真正启动发生在模型调用 delegate 时。
        """

        if not self.enable_delegate_tool:
            return
        if "delegate" in self.session.tool_registry.names():
            return
        project_root = None
        if self.session.permission_manager is not None:
            project_root = self.session.permission_manager.policy.project_root
        runner = SubagentRunner(
            store=self.session.store,
            provider=self.provider,
            tools=[tool for tool in self.session.tool_registry.tools() if tool.name != "delegate"],
            project_root=project_root,
            agents_md=self.session.agents_md,
            skill_catalog=self.session.skill_catalog,
            permission_manager=self.session.permission_manager,
            sandbox_access=self.session.sandbox_access,
            request_options=self.request_options,
        )
        self.session.tool_registry.register(
            create_delegate_tool(
                runner,
                parent_session_id=self.session.session_id,
                parent_task_hash=self.session.runtime_state.active_task_hash,
            )
        )

    def _begin_turn(self, *, new_user_turn: bool = True) -> None:
        """新输入清零本轮预算/证据；权限恢复传 False，保留同一轮已用的计数。

        new_user_turn 不表示切换任务。MCP 激活集合按新输入清空，任务锚点则由任务边界
        服务维护。遥测已暂停但仍 active 时，恢复不会在这里重置起始时刻。
        """
        if new_user_turn:
            self._active_mcp_tool_names.clear()
            self.provider_call_count = 0
            self.turn_started_at = self.clock()
            self.telemetry_started_at = self.telemetry_clock()
            self._task_plan_reconciliation_attempted = False
            self._completion_gate_attempted = False
            self._completion_gate_attempt_count = 0
            self._tool_rounds_completed = 0
            self.execution_evidence.reset()
            self.stagnation_guard.reset()
            self.turn_telemetry.begin(
                turn_number=self.session.current_turn + 1,
                started_at=self.telemetry_started_at,
            )
        elif not self.turn_telemetry.active:
            self.turn_started_at = self.clock()
            self.telemetry_started_at = self.telemetry_clock()
            self.turn_telemetry.begin(
                turn_number=self.session.current_turn,
                started_at=self.telemetry_started_at,
            )

    def _validate_tool_call(self, tool_call: ToolCall) -> ToolResult | None:
        """执行前复核 MCP 激活、运行模式和可选的停滞护栏；返回失败结果即阻止执行。

        None 只代表通过这些路由检查，后续 ToolExecutor 仍需进行权限等检查。
        """
        validation_error = self._validate_mcp_tool_call(tool_call)
        if validation_error is not None:
            return validation_error
        route_error = self._runtime_tool_visibility_error(tool_call)
        if route_error is not None:
            return route_error
        if not self.runtime_capabilities.enable_stagnation_guard:
            return None
        return self.stagnation_guard.validate(tool_call)

    def _observe_tool_result(self, tool_call: ToolCall, result: ToolResult) -> None:
        """执行器回调：累计遥测、更新 MCP 激活与可选执行证据，不再执行一次工具。"""
        self._observe_mcp_search_result(tool_call, result)
        self.turn_telemetry.observe_tool_result(
            tool_call,
            result,
            elapsed_seconds=self._turn_elapsed_seconds(),
        )
        if not (
            self.runtime_capabilities.enable_completion_gate
            or self.runtime_capabilities.enable_stagnation_guard
        ):
            return
        if (
            tool_call.name == "task_boundary"
            and result.ok
            and result.data.get("should_trigger_compaction")
        ):
            self.execution_evidence.reset()
            self.stagnation_guard.reset()
            self._completion_gate_attempted = False
            self._completion_gate_attempt_count = 0
            return
        if self.runtime_capabilities.enable_completion_gate:
            self.execution_evidence.observe(tool_call, result)
            if self._completion_gate_attempted and is_mutation_result(tool_call, result):
                self._completion_gate_attempted = False
        if self.runtime_capabilities.enable_stagnation_guard:
            append_guidance(result, self.stagnation_guard.observe(tool_call, result))

    def _runtime_tool_visibility_error(self, tool_call: ToolCall) -> ToolResult | None:
        """复核模型返回的工具在当前运行模式是否可用；不允许的调用变成错误观察结果。"""
        if tool_call.name == "think" and not self.runtime_capabilities.expose_think_tool:
            return make_error_result(
                tool_call.name,
                "当前运行模式未暴露 think 工具。",
                tool_not_routed=True,
            )
        if tool_call.name == "ask_user" and not self.runtime_capabilities.allow_user_input:
            return make_error_result(
                tool_call.name,
                "当前运行模式为非交互 benchmark，未暴露 ask_user；请基于题面自主决策并验证目标。",
                tool_not_routed=True,
            )
        if (
            tool_call.name == "web_search"
            and not self.runtime_capabilities.expose_web_search_tool
        ):
            return make_error_result(
                tool_call.name,
                "当前 benchmark 协议未暴露 web_search；只能 fetch 题面已知的明确 URL。",
                tool_not_routed=True,
            )
        if (
            tool_call.name in PLANNING_TOOL_NAMES
            and not self.runtime_capabilities.expose_planning_tools
            and self.session.rebuild_view().task_plan is None
        ):
            return make_error_result(
                tool_call.name,
                "当前任务被路由为简单单任务，未暴露 TaskPlan 工具；请直接完成并验证目标。",
                tool_not_routed=True,
            )
        if tool_call.name == "delegate" and not self.enable_delegate_tool:
            return make_error_result(
                tool_call.name,
                "当前任务未启用子代理；请使用主代理工具直接完成。",
                tool_not_routed=True,
            )
        return None

    def _validate_mcp_tool_call(self, tool_call: ToolCall) -> ToolResult | None:
        """拒绝本轮尚未通过 mcp_tool_search 激活的 MCP 工具，防止绕过渐进暴露流程。"""
        if tool_call.name not in self._mcp_tool_names:
            return None
        if tool_call.name in self._active_mcp_tool_names:
            return None
        return make_error_result(
            tool_call.name,
            "MCP tool is not active for this user turn. Call mcp_tool_search first.",
            mcp_activation_required=True,
        )

    def _observe_mcp_search_result(
        self,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> None:
        """从成功的 MCP 搜索结果中登记本轮激活工具，供下一次 schema 构造使用。"""
        # 搜索返回的 activated_tools 必须属于实际注册的 MCP 工具；激活后才加入可见
        # schema，避免一次性把所有外部工具声明都塞进每次模型请求。
        if tool_call.name != "mcp_tool_search" or not result.ok:
            return
        payload = result.data.get("mcp_tool_search")
        if not isinstance(payload, dict):
            return
        activated = payload.get("activated_tools")
        if not isinstance(activated, list):
            return
        self._active_mcp_tool_names.update(
            name
            for name in activated
            if isinstance(name, str) and name in self._mcp_tool_names
        )

    def _append_pending_guidance(self) -> None:
        """在下一次模型请求前接收上层追加指导；不会中途修改已发出的模型请求。"""
        if self.guidance_provider is None:
            return
        guidance_items = self.guidance_provider()
        for content in guidance_items:
            text = content.strip()
            if text:
                self.session.append_user_message(text)

    def _append_background_notifications(self) -> None:
        """在下一次模型请求前，把当前父会话的已完成后台任务转换成历史通知。

        原后台 tool_call 已由“任务已启动”占位结果闭合；完成通知以独立
        background_notification 事件保存，并投影为 user 消息，不再复用原 tool_call_id
        写第二份 tool result。模型在后续请求中看到通知，不是完成时直接打断模型生成。
        """

        if self.background_manager is None:
            return
        for notification in self.background_manager.collect_completed(session_id=self.session.session_id):
            self.session.append_background_notification(
                content=render_task_notification(notification),
                job_id=notification.job_id,
                tool_name=notification.tool_name,
                status=notification.status,
                task_id=notification.task_id,
                observed_revision=notification.observed_revision,
            )

    def _check_provider_call_limit(self) -> None:
        """若本 turn 已耗尽模型请求预算，抛内部停止信号，阻止发出下一次请求。"""
        # 检查发生在计数增加之前：计数达到上限后，不再发起下一次请求。
        limit = self.limits.max_provider_calls
        if limit is not None and self.provider_call_count >= limit:
            raise _AgentLoopLimitReached(AgentLoopStopReason.PROVIDER_CALL_LIMIT)

    def _reserve_provider_call(self) -> None:
        """每次实际请求前预占一次预算，失败尝试也计数；与工具轮数不是同一计数器。"""
        self._check_provider_call_limit()
        self.provider_call_count += 1
        self.turn_telemetry.observe_provider_call()

    def _turn_elapsed_seconds(self) -> float:
        """返回当前 turn 的遥测耗时；尚未开始时返回 0，并避免时钟差出现负值。"""
        if self.telemetry_started_at is None:
            return 0.0
        return max(0.0, self.telemetry_clock() - self.telemetry_started_at)

    def _persist_turn_telemetry(
        self,
        *,
        status: str,
        stop_reason: str,
        finalize: bool,
        provider_failure_category: str | None = None,
    ) -> None:
        """把本轮状态、停止原因和耗时快照追加到 session 事件日志。"""
        # 遥测是运行记录，不是发给模型的聊天内容。暂停时 finalize=False，结束/异常时
        # finalize=True，让恢复流程和最终耗时有一致的统计边界。
        payload = self.turn_telemetry.snapshot(
            status=status,
            stop_reason=stop_reason,
            elapsed_seconds=self._turn_elapsed_seconds(),
            provider_failure_category=provider_failure_category,
            finalize=finalize,
        )
        if payload is not None:
            self.session.writer.append_agent_turn_telemetry(payload)

    def _persist_errored_turn(self, exc: BaseException) -> None:
        """把未被本层转换为响应的异常记入遥测；区分用户中断与 provider/程序错误。"""
        if not self.turn_telemetry.active:
            return
        if (
            isinstance(exc, AgentCancelledError | KeyboardInterrupt)
            or type(exc).__name__ == "CancelledError"
        ):
            self._persist_turn_telemetry(
                status="interrupted",
                stop_reason="interrupted",
                finalize=True,
            )
            return
        provider_category = exc.kind.value if isinstance(exc, ProviderError) else None
        stop_reason = provider_category or type(exc).__name__
        self._persist_turn_telemetry(
            status="errored",
            stop_reason=stop_reason,
            provider_failure_category=provider_category,
            finalize=True,
        )

    def _check_turn_timeout(self) -> None:
        """在检查点判断本轮是否超时；不会强制打断正在阻塞的 SDK 请求或 shell 命令。"""
        limit = self.limits.max_turn_seconds
        if limit is None or self.turn_started_at is None:
            return
        if self.clock() - self.turn_started_at >= limit:
            raise _AgentLoopLimitReached(AgentLoopStopReason.TURN_TIMEOUT)

    def _is_cancelled(self) -> bool:
        """只查询取消标记，不抛异常；用于在结束路径上决定最终响应状态。"""
        return self.cancellation_token is not None and self.cancellation_token.is_cancelled

    def _check_cancelled(self) -> None:
        """在安全检查点把取消标记转成 AgentCancelledError，交给循环统一收尾。"""
        if self.cancellation_token is not None:
            self.cancellation_token.raise_if_cancelled()

    def _drop_unsupported_tool_calls(self, response: ChatResponse) -> ChatResponse:
        """兜底保护：不支持工具的 provider 理论上不该返回 tool_calls。

        如果兼容站行为异常仍返回了 tool_calls，这里把它们丢弃并记录 diagnostics，避免
        agent 执行一个 provider 能力声明之外的工具链。
        """

        capabilities = getattr(self.provider, "capabilities", None)
        if capabilities is None or capabilities.supports_tools or not response.tool_calls:
            return response
        self._drop_unsupported_tool_call_stream_events()
        response.diagnostics.warnings.append("provider returned tool_calls even though supports_tools is false; tool calls were ignored")
        return ChatResponse(
            provider=response.provider,
            model=response.model,
            content=response.content or "当前 provider 不支持 tool calling，已忽略模型返回的工具调用。",
            tool_calls=[],
            finish_reason="error",
            usage=response.usage,
            diagnostics=response.diagnostics,
            raw=response.raw,
        )

    def _drop_unsupported_tool_call_stream_events(self) -> None:
        """provider 声明不支持工具时，移除本次 stream 中对应的局部工具调用展示事件。"""
        if not self.last_stream_events:
            return
        self.last_stream_events = [event for event in self.last_stream_events if event.kind not in {"tool_call_started", "tool_call_delta", "tool_call_completed"}]

    def _tool_round_limit_response(self, response: ChatResponse) -> ChatResponse:
        """工具轮次上限命中后，只保存纯文本说明，避免写入未执行的 tool_call。"""

        return self._limit_response(AgentLoopStopReason.TOOL_ROUND_LIMIT, raw=response.raw)

    def _limit_response(self, reason: AgentLoopStopReason, *, raw: dict | None = None) -> ChatResponse:
        """把内部预算停止原因映射成无 tool_calls 的普通响应，避免遗留未执行调用。"""
        messages = {
            AgentLoopStopReason.PROVIDER_CALL_LIMIT: (f"provider 调用次数达到上限（max_provider_calls={self.limits.max_provider_calls}），已停止继续执行。"),
            AgentLoopStopReason.TURN_TIMEOUT: (f"本轮任务耗时达到上限（max_turn_seconds={self.limits.max_turn_seconds}），已停止继续执行。"),
            AgentLoopStopReason.TOOL_ROUND_LIMIT: (f"工具调用轮次达到上限（max_tool_rounds={self.limits.max_tool_rounds}），已停止继续执行工具。"),
        }
        return ChatResponse(
            provider=self.provider.name,
            model=self.provider.model,
            content=messages[reason],
            tool_calls=[],
            finish_reason=reason.value,
            raw=raw,
        )

    def _interrupted_response(self) -> ChatResponse:
        """构造用户可见的中断响应；未知工具执行结果由单独的 settlement 记录。"""
        return ChatResponse(
            provider=self.provider.name,
            model=self.provider.model,
            content="当前任务已中断。",
            tool_calls=[],
            finish_reason="interrupted",
            raw={"interrupted": True},
        )


class _AgentLoopLimitReached(Exception):
    """内部预算控制信号；在循环边界转换为停止响应，区别于外部 provider 失败。"""

    def __init__(self, reason: AgentLoopStopReason) -> None:
        """保存具体停止原因，供工具循环转换成对应的用户可见响应。"""
        super().__init__(reason.value)
        self.reason = reason
