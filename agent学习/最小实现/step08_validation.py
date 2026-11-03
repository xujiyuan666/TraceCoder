"""第 8 步：工具 schema 不是验证器，执行前还要校验参数。"""
import argparse
import json
from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from support import Trace, create_registry


class AddArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: int = Field(description="第一个整数")
    b: int = Field(description="第二个整数")


def schema_and_execute(raw_arguments, trace: Trace):
    trace.emit("RAW_ARGUMENTS", arguments=raw_arguments)
    try:
        if isinstance(raw_arguments, str):
            raw_arguments = json.loads(raw_arguments)
        args = AddArgs.model_validate(raw_arguments)
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        trace.emit("VALIDATION_ERROR", error=str(exc))
        return {"ok": False, "error": str(exc)}
    result = create_registry().execute("add", args.model_dump())
    trace.emit("VALIDATED_AND_EXECUTED", arguments=args.model_dump(), result=asdict(result))
    return asdict(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=["ok", "json", "missing", "extra", "type"], nargs="?", default="ok")
    args = parser.parse_args()
    cases = {
        "ok": {"a": 2, "b": 3},
        "json": '{"a": 2, "b": 3}',
        "missing": {"a": 2},
        "extra": {"a": 2, "b": 3, "c": 4},
        "type": {"a": "two", "b": 3},
    }
    schema_and_execute(cases[args.case], Trace(True))


if __name__ == "__main__":
    main()
