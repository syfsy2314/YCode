"""Hook 生命周期事件上下文构造。"""

from collections.abc import Mapping
from pathlib import Path

from ycode.context.models import ContextCompactionReport
from ycode.core.messages import ChatMessage, ToolCallBlock, thaw_json
from ycode.hooks.models import HookEvent, HookEventName
from ycode.tools.contracts import ToolExecutionRecord


class HookContextFactory:
    def __init__(self, project: Path, session_id: str) -> None:
        self._project = project.resolve()
        self._session_id = session_id

    def simple(self, name: HookEventName, **values: object) -> HookEvent:
        context = self._base(name)
        context.update(values)
        return HookEvent(name, context)

    def message(
        self,
        name: HookEventName,
        turn_id: str,
        message: ChatMessage,
    ) -> HookEvent:
        return self.simple(
            name,
            turn={"id": turn_id},
            message={"role": message.role, "content": message.text},
        )

    def tool_before(
        self,
        turn_id: str,
        call: ToolCallBlock,
        arguments: Mapping[str, object],
    ) -> HookEvent:
        tool = {"id": call.id, "name": call.name, "arguments": thaw_json(arguments)}
        values: dict[str, object] = {"turn": {"id": turn_id}, "tool": tool}
        path = arguments.get("path")
        if isinstance(path, str):
            values["file"] = {"path": path}
        return self.simple(HookEventName.TOOL_BEFORE_EXECUTE, **values)

    def tool_after(self, turn_id: str, record: ToolExecutionRecord) -> HookEvent:
        arguments = thaw_json(record.call.arguments)
        tool = {
            "id": record.call.id,
            "name": record.call.name,
            "arguments": arguments,
            "result": {
                "content": record.result.content,
                "is_error": record.result.is_error,
                "metadata": thaw_json(record.result.metadata),
            },
        }
        values: dict[str, object] = {"turn": {"id": turn_id}, "tool": tool}
        if isinstance(arguments, dict) and isinstance(arguments.get("path"), str):
            values["file"] = {"path": arguments["path"]}
        return self.simple(HookEventName.TOOL_AFTER_EXECUTE, **values)

    def compacted(self, turn_id: str, report: ContextCompactionReport) -> HookEvent:
        return self.simple(
            HookEventName.CONTEXT_COMPACTED,
            turn={"id": turn_id},
            context={
                "before_tokens": report.before_tokens,
                "after_tokens": report.after_tokens,
                "manual": report.manual,
            },
        )

    def _base(self, name: HookEventName) -> dict[str, object]:
        return {
            "event": {"name": name.value},
            "project": {"path": str(self._project)},
            "session": {"id": self._session_id},
        }
