"""完整模型请求的本地 Token 估算。"""

import json
import math
from collections.abc import Mapping
from typing import Any

from ycode.context.models import TokenEstimate
from ycode.core.messages import (
    ChatMessage,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    thaw_json,
)
from ycode.core.provider import AgentModelRequest
from ycode.tools.contracts import ToolDefinition


def _message_value(message: ChatMessage) -> dict[str, object]:
    content: list[dict[str, object]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingBlock):
            content.append(
                {"type": "thinking", "thinking": block.text, "signature": block.signature}
            )
        elif isinstance(block, RedactedThinkingBlock):
            content.append({"type": "redacted_thinking", "data": block.data})
        elif isinstance(block, ToolCallBlock):
            content.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": thaw_json(block.arguments),
                }
            )
        elif isinstance(block, ToolResultBlock):
            content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_call_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return {"role": message.role, "content": content}


def _tool_value(tool: ToolDefinition[Any]) -> dict[str, object]:
    name = tool.name
    description = tool.description
    schema = tool.input_schema
    if isinstance(schema, Mapping):
        schema = thaw_json(schema)
    return {"name": name, "description": description, "input_schema": schema}


def _request_bytes(request: AgentModelRequest) -> int:
    value = {
        "system": list(request.system_prompt),
        "supplements": list(request.supplements),
        "tools": [_tool_value(tool) for tool in request.tools],
        "messages": [_message_value(message) for message in request.messages],
        "continuation_messages": [
            _message_value(message) for message in request.continuation_messages
        ],
    }
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return len(serialized.encode("utf-8"))


class TokenEstimator:
    """使用保守字节换算，并根据实际 usage 向上校准。"""

    def __init__(self) -> None:
        self._calibration_ratio = 1.0

    @property
    def calibration_ratio(self) -> float:
        return self._calibration_ratio

    def estimate(self, request: AgentModelRequest) -> TokenEstimate:
        if not isinstance(request, AgentModelRequest):
            raise TypeError("Token 估算必须接收 AgentModelRequest")
        local_tokens = max(1, math.ceil(_request_bytes(request) / 3))
        calibrated_tokens = math.ceil(local_tokens * self._calibration_ratio)
        return TokenEstimate(local_tokens, calibrated_tokens)

    def observe(self, local_tokens: int, actual_input_tokens: int) -> None:
        for value in (local_tokens, actual_input_tokens):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("Token 校准值必须是正整数")
        self._calibration_ratio = max(
            self._calibration_ratio,
            actual_input_tokens / local_tokens,
        )
