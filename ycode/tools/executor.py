"""工具查找、参数校验、超时与安全错误转换。"""

import asyncio
from collections.abc import Mapping

from pydantic import ValidationError

from ycode.core.messages import ToolCallBlock, thaw_json
from ycode.tools.contracts import (
    ToolAccess,
    ToolContext,
    ToolExecutionResult,
)
from ycode.tools.errors import ToolError
from ycode.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        call: ToolCallBlock,
        context: ToolContext,
        allowed_access: frozenset[ToolAccess],
    ) -> ToolExecutionResult:
        tool = self._registry.get(call.name)
        if tool is None:
            return _error_result("unknown_tool", f"未知工具：{call.name}。")
        if tool.definition.access not in allowed_access:
            return _error_result("access_denied", "当前模式不允许执行该工具。")

        arguments = thaw_json(call.arguments)
        if not isinstance(arguments, Mapping):
            return _error_result("invalid_arguments", "工具参数必须是 JSON object。")
        try:
            validated = tool.definition.arguments_model.model_validate(dict(arguments))
        except ValidationError as error:
            details = [
                {
                    "field": ".".join(str(item) for item in issue["loc"]),
                    "message": issue["msg"],
                    "type": issue["type"],
                }
                for issue in error.errors(include_url=False, include_input=False)
            ]
            return _error_result(
                "invalid_arguments",
                "工具参数校验失败。",
                metadata={"details": details},
            )

        try:
            async with asyncio.timeout(tool.timeout_seconds):
                return await tool.execute(validated, context)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return _error_result(
                "timeout",
                "工具执行超时。",
                metadata={"timeout_seconds": tool.timeout_seconds},
            )
        except ToolError as error:
            metadata = dict(thaw_json(error.metadata))
            return _error_result(error.code, error.message, metadata=metadata)
        except Exception:
            return _error_result("internal_error", "工具执行发生内部错误。")


def _error_result(
    code: str,
    message: str,
    *,
    metadata: Mapping[str, object] | None = None,
) -> ToolExecutionResult:
    details = dict(metadata or {})
    details["error_code"] = code
    return ToolExecutionResult(
        content=message,
        is_error=True,
        metadata=details,
    )
