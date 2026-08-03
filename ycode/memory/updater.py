"""退出时通过隔离模型请求整理项目记忆。"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING

from ycode.core.events import (
    StopReason,
    StreamEnd,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)
from ycode.core.messages import (
    ChatMessage,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    thaw_json,
)
from ycode.core.provider import AgentChatProvider, AgentModelRequest
from ycode.memory.models import (
    MemoryAction,
    MemoryEntry,
    MemoryOperation,
    MemorySnapshot,
    MemoryType,
    MemoryUpdatePlan,
)

if TYPE_CHECKING:
    from ycode.session.models import SessionCommit


class MemoryUpdateError(Exception):
    """模型没有返回可安全执行的记忆操作。"""


def load_memory_update_prompt() -> str:
    return files("ycode.memory.resources").joinpath("update.md").read_text(encoding="utf-8")


def build_memory_transcript(
    current: MemorySnapshot,
    conversations: tuple[SessionCommit, ...],
) -> str:
    memory = [
        {
            "path": entry.path,
            "name": entry.name,
            "description": entry.description,
            "type": entry.type.value,
            "body": entry.body,
        }
        for entry in current.entries
    ]
    sessions = [
        {
            "session_id": commit.session_id,
            "turn_id": commit.turn_id,
            "messages": [
                {
                    "timestamp": item.created_at.isoformat().replace("+00:00", "Z"),
                    **_encode_transcript_message(item.message),
                }
                for item in commit.messages
            ],
        }
        for commit in conversations
    ]
    return json.dumps(
        {"current_memory": memory, "new_conversations": sessions},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _encode_transcript_message(message: ChatMessage) -> dict[str, object]:
    blocks: list[dict[str, object]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingBlock):
            blocks.append({"type": "thinking", "text": block.text})
        elif isinstance(block, RedactedThinkingBlock):
            blocks.append({"type": "redacted_thinking"})
        elif isinstance(block, ToolCallBlock):
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": thaw_json(block.arguments),
                }
            )
        elif isinstance(block, ToolResultBlock):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_call_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return {"role": message.role, "content": blocks}


def parse_memory_update(value: str) -> MemoryUpdatePlan:
    try:
        data = json.loads(value)
    except json.JSONDecodeError as error:
        raise MemoryUpdateError("记忆整理响应不是单个 JSON 对象") from error
    if not isinstance(data, dict) or set(data) != {"operations"}:
        raise MemoryUpdateError("记忆整理响应结构无效")
    operations = data["operations"]
    if not isinstance(operations, list):
        raise MemoryUpdateError("记忆操作列表无效")
    parsed: list[MemoryOperation] = []
    try:
        for value in operations:
            if not isinstance(value, dict):
                raise ValueError
            action = MemoryAction(value.get("action"))
            path = value.get("path")
            if not isinstance(path, str):
                raise ValueError
            if action is MemoryAction.DELETE:
                if set(value) != {"action", "path"}:
                    raise ValueError
                parsed.append(MemoryOperation(action, path))
                continue
            if set(value) != {"action", "path", "entry"} or not isinstance(value["entry"], dict):
                raise ValueError
            entry_data = value["entry"]
            if set(entry_data) != {"path", "name", "description", "type", "body"}:
                raise ValueError
            entry = MemoryEntry(
                path=entry_data["path"],
                name=entry_data["name"],
                description=entry_data["description"],
                type=MemoryType(entry_data["type"]),
                body=entry_data["body"],
            )
            parsed.append(MemoryOperation(action, path, entry))
        return MemoryUpdatePlan(tuple(parsed))
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise MemoryUpdateError("记忆操作字段无效") from error


class MemoryUpdater:
    def __init__(self, provider: AgentChatProvider) -> None:
        self._provider = provider

    async def analyze(
        self,
        current: MemorySnapshot,
        conversations: tuple[SessionCommit, ...],
    ) -> MemoryUpdatePlan:
        if not conversations:
            return MemoryUpdatePlan()
        request = AgentModelRequest(
            messages=(ChatMessage.user_text(build_memory_transcript(current, conversations)),),
            system_prompt=(load_memory_update_prompt(),),
            tools=(),
            max_output_tokens=8_000,
            thinking_enabled=False,
        )
        text: list[str] = []
        ended = False
        async for event in self._provider.stream_agent(request):
            if ended:
                raise MemoryUpdateError("记忆整理完成后出现额外事件")
            if isinstance(event, TextDelta):
                text.append(event.text)
            elif isinstance(event, StreamEnd):
                if event.stop_reason is not StopReason.END_TURN:
                    raise MemoryUpdateError("记忆整理没有正常结束")
                ended = True
            elif isinstance(
                event,
                ThinkingDelta | ThinkingComplete | ToolCallStart | ToolCallDelta | ToolCallComplete,
            ):
                raise MemoryUpdateError("记忆整理响应包含不允许的事件")
        if not ended:
            raise MemoryUpdateError("记忆整理响应缺少完成事件")
        return parse_memory_update("".join(text))
