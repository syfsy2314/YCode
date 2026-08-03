"""结构化消息与版本化会话记录的 JSON 编解码。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast

from ycode.context.models import ConversationMemory
from ycode.core.messages import (
    ChatMessage,
    ContentBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    freeze_json,
    thaw_json,
)
from ycode.session.models import (
    SESSION_FORMAT_VERSION,
    ContextCheckpointRecord,
    SessionMessageRecord,
    SessionRecord,
    TurnCommitRecord,
)


class SessionCodecError(ValueError):
    """会话记录不是受支持的稳定格式。"""


def encode_message(message: ChatMessage) -> dict[str, object]:
    return {
        "role": message.role,
        "content": [_encode_block(block) for block in message.content],
    }


def decode_message(value: object) -> ChatMessage:
    data = _object(value, "message")
    if set(data) != {"role", "content"} or not isinstance(data["content"], list):
        raise SessionCodecError("消息结构无效")
    role = data["role"]
    if role not in {"user", "assistant"}:
        raise SessionCodecError("消息角色无效")
    try:
        blocks = tuple(_decode_block(item) for item in data["content"])
        return ChatMessage(cast("str", role), blocks)
    except (TypeError, ValueError) as error:
        raise SessionCodecError("消息内容块无效") from error


def encode_record(record: SessionRecord) -> str:
    common: dict[str, object] = {
        "version": record.version,
        "session_id": record.session_id,
        "timestamp": _encode_time(record.timestamp),
    }
    if isinstance(record, SessionMessageRecord):
        data = {
            **common,
            "type": "message",
            "turn_id": record.turn_id,
            "message": encode_message(record.message),
        }
    elif isinstance(record, TurnCommitRecord):
        data = {
            **common,
            "type": "turn_commit",
            "turn_id": record.turn_id,
            "message_count": record.message_count,
        }
    elif isinstance(record, ContextCheckpointRecord):
        data = {
            **common,
            "type": "context_checkpoint",
            "covered_turn_id": record.covered_turn_id,
            "memory": record.memory.summary,
            "retained_history": [encode_message(message) for message in record.retained_history],
        }
    else:
        raise TypeError("不支持的会话记录")
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def decode_record(line: str) -> SessionRecord:
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, TypeError) as error:
        raise SessionCodecError("JSON 无法解析") from error
    data = _object(data, "record")
    if data.get("version") != SESSION_FORMAT_VERSION:
        raise SessionCodecError("会话记录版本不受支持")
    record_type = data.get("type")
    try:
        common = {
            "version": data["version"],
            "session_id": data["session_id"],
            "timestamp": _decode_time(data["timestamp"]),
        }
        if record_type == "message" and set(data) == {
            "version",
            "type",
            "session_id",
            "turn_id",
            "timestamp",
            "message",
        }:
            return SessionMessageRecord(
                **common,
                turn_id=data["turn_id"],
                message=decode_message(data["message"]),
            )
        if record_type == "turn_commit" and set(data) == {
            "version",
            "type",
            "session_id",
            "turn_id",
            "timestamp",
            "message_count",
        }:
            return TurnCommitRecord(
                **common,
                turn_id=data["turn_id"],
                message_count=data["message_count"],
            )
        if record_type == "context_checkpoint" and set(data) == {
            "version",
            "type",
            "session_id",
            "covered_turn_id",
            "timestamp",
            "memory",
            "retained_history",
        }:
            retained = data["retained_history"]
            if not isinstance(retained, list) or not isinstance(data["memory"], str):
                raise SessionCodecError("检查点结构无效")
            return ContextCheckpointRecord(
                **common,
                covered_turn_id=data["covered_turn_id"],
                memory=ConversationMemory(data["memory"]),
                retained_history=tuple(decode_message(item) for item in retained),
            )
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCodecError("会话记录字段无效") from error
    raise SessionCodecError("会话记录类型或字段无效")


def _encode_block(block: ContentBlock) -> dict[str, object]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "text": block.text, "signature": block.signature}
    if isinstance(block, RedactedThinkingBlock):
        return {"type": "redacted_thinking", "data": block.data}
    if isinstance(block, ToolCallBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": thaw_json(block.arguments),
        }
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_call_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    raise TypeError("不支持的消息内容块")


def _decode_block(value: object) -> ContentBlock:
    data = _object(value, "block")
    block_type = data.get("type")
    if block_type == "text" and set(data) == {"type", "text"}:
        return TextBlock(_string(data["text"]))
    if block_type == "thinking" and set(data) == {"type", "text", "signature"}:
        return ThinkingBlock(_string(data["text"]), _string(data["signature"]))
    if block_type == "redacted_thinking" and set(data) == {"type", "data"}:
        return RedactedThinkingBlock(_string(data["data"]))
    if block_type == "tool_use" and set(data) == {"type", "id", "name", "input"}:
        arguments = freeze_json(data["input"])
        if not isinstance(arguments, dict) and not hasattr(arguments, "items"):
            raise SessionCodecError("工具参数必须是 object")
        return ToolCallBlock(_string(data["id"]), _string(data["name"]), arguments)
    if block_type == "tool_result" and set(data) == {"type", "tool_use_id", "content", "is_error"}:
        if not isinstance(data["is_error"], bool):
            raise SessionCodecError("工具结果错误标记无效")
        return ToolResultBlock(
            _string(data["tool_use_id"]),
            _string(data["content"]),
            data["is_error"],
        )
    raise SessionCodecError("未知内容块")


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SessionCodecError(f"{name} 必须是 JSON object")
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise SessionCodecError("字段必须是字符串")
    return value


def _encode_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _decode_time(value: object) -> datetime:
    text = _string(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise SessionCodecError("时间格式无效") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise SessionCodecError("时间必须是 UTC")
    return parsed
