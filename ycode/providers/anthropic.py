"""Anthropic Messages API 的结构化流式适配器。"""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from ycode.config.models import ProviderConfig
from ycode.core.events import (
    StopReason,
    StreamEnd,
    StreamEvent,
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
from ycode.errors import ProviderError

MAX_TOKENS = 16_000


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _string_field(value: object, name: str) -> str:
    result = _field(value, name, "")
    return result if isinstance(result, str) else ""


@dataclass(slots=True)
class _TextState:
    pass


@dataclass(slots=True)
class _ThinkingState:
    text_parts: list[str] = field(default_factory=list)
    signature_parts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _RedactedThinkingState:
    data: str


@dataclass(slots=True)
class _ToolCallState:
    id: str
    name: str
    argument_parts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _IgnoredThinkingState:
    pass


type _BlockState = (
    _TextState | _ThinkingState | _RedactedThinkingState | _ToolCallState | _IgnoredThinkingState
)


class AnthropicProvider:
    def __init__(self, config: ProviderConfig, *, client: Any | None = None) -> None:
        self._config = config
        self.client = client or AsyncAnthropic(
            api_key=config.api_key.get_secret_value(),
            base_url=config.base_url,
            max_retries=0,
        )
        self._closed = False

    @staticmethod
    def _messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            if len(message.content) == 1 and isinstance(message.content[0], TextBlock):
                result.append({"role": message.role, "content": message.content[0].text})
                continue

            blocks: list[dict[str, Any]] = []
            saw_text = False
            for block in message.content:
                if isinstance(block, TextBlock):
                    saw_text = True
                    blocks.append({"type": "text", "text": block.text})
                elif isinstance(block, ThinkingBlock):
                    value: dict[str, Any] = {"type": "thinking", "thinking": block.text}
                    if block.signature:
                        value["signature"] = block.signature
                    blocks.append(value)
                elif isinstance(block, RedactedThinkingBlock):
                    blocks.append({"type": "redacted_thinking", "data": block.data})
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
                    if saw_text:
                        raise ProviderError(
                            "request",
                            "Anthropic 工具结果必须位于用户文本之前。",
                            False,
                        )
                    value = {
                        "type": "tool_result",
                        "tool_use_id": block.tool_call_id,
                        "content": block.content,
                    }
                    if block.is_error:
                        value["is_error"] = True
                    blocks.append(value)
            result.append({"role": message.role, "content": blocks})
        return result

    @staticmethod
    def _stop_reason(reason: str | None) -> StopReason:
        return {
            "end_turn": StopReason.END_TURN,
            "tool_use": StopReason.TOOL_USE,
            "max_tokens": StopReason.MAX_TOKENS,
            "stop_sequence": StopReason.STOP_SEQUENCE,
            "refusal": StopReason.CONTENT_FILTER,
        }.get(reason or "", StopReason.UNKNOWN)

    @staticmethod
    def _provider_error(error: Exception) -> ProviderError:
        if isinstance(error, anthropic.AuthenticationError):
            return ProviderError(
                "authentication", "Anthropic API 认证失败，请检查 API Key。", False
            )
        if isinstance(error, anthropic.RateLimitError):
            return ProviderError("rate_limit", "Anthropic API 请求过于频繁，请稍后重试。", True)
        if isinstance(error, anthropic.APITimeoutError):
            return ProviderError("network", "连接 Anthropic API 超时，请重试。", True)
        if isinstance(error, anthropic.APIConnectionError):
            return ProviderError("network", "无法连接 Anthropic API，请检查网络和地址。", True)
        if isinstance(error, anthropic.APIStatusError):
            if error.status_code >= 500:
                return ProviderError("server", "Anthropic 服务暂时不可用，请稍后重试。", True)
            return ProviderError(
                "request",
                "Anthropic 拒绝了请求，请检查模型是否支持当前配置（包括 Thinking）。",
                False,
            )
        return ProviderError("stream", "Anthropic 响应流意外中断，请重试。", True)

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamEvent]:
        request: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": MAX_TOKENS,
            "messages": self._messages(messages),
            "stream": True,
        }
        request["thinking"] = (
            {"type": "adaptive", "display": "summarized"}
            if self._config.thinking
            else {"type": "disabled"}
        )

        message_started = False
        completed = False
        provider_reason = ""
        blocks: dict[int, _BlockState] = {}
        try:
            stream = await self.client.messages.create(**request)
            async for event in stream:
                event_type = str(_field(event, "type", ""))
                if completed:
                    raise ProviderError(
                        "stream",
                        "Anthropic 完成后返回了额外内容，请重试。",
                        True,
                    )
                if event_type == "message_start":
                    if message_started:
                        raise ProviderError(
                            "stream",
                            "Anthropic 响应流包含重复的消息开始事件。",
                            False,
                        )
                    message_started = True
                    continue

                if event_type == "content_block_start":
                    if not message_started:
                        raise ProviderError(
                            "stream",
                            "Anthropic 内容块早于消息开始。",
                            False,
                        )
                    index = int(_field(event, "index", -1))
                    if index < 0 or index in blocks:
                        raise ProviderError(
                            "stream",
                            "Anthropic 返回了无效的内容块索引。",
                            False,
                        )
                    block = _field(event, "content_block")
                    block_type = str(_field(block, "type", ""))
                    if block_type == "text":
                        blocks[index] = _TextState()
                        initial = _string_field(block, "text")
                        if initial:
                            yield TextDelta(index, initial)
                    elif block_type == "thinking":
                        if not self._config.thinking:
                            blocks[index] = _IgnoredThinkingState()
                            continue
                        state = _ThinkingState()
                        blocks[index] = state
                        initial = _string_field(block, "thinking")
                        signature = _string_field(block, "signature")
                        if initial:
                            state.text_parts.append(initial)
                            yield ThinkingDelta(index, initial)
                        if signature:
                            state.signature_parts.append(signature)
                    elif block_type == "redacted_thinking":
                        if not self._config.thinking:
                            blocks[index] = _IgnoredThinkingState()
                            continue
                        blocks[index] = _RedactedThinkingState(data=_string_field(block, "data"))
                    elif block_type == "tool_use":
                        tool_id = _string_field(block, "id")
                        name = _string_field(block, "name")
                        if not tool_id or not name:
                            raise ProviderError(
                                "stream",
                                "Anthropic 返回了无效的工具调用标识。",
                                False,
                            )
                        state = _ToolCallState(id=tool_id, name=name)
                        blocks[index] = state
                        yield ToolCallStart(index, tool_id, name)
                        initial_input = _field(block, "input", {})
                        if initial_input:
                            if not isinstance(initial_input, Mapping):
                                raise ProviderError(
                                    "stream",
                                    "Anthropic 返回的工具参数必须是 JSON object。",
                                    False,
                                )
                            initial_json = json.dumps(
                                initial_input,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            state.argument_parts.append(initial_json)
                            yield ToolCallDelta(index, initial_json)
                    else:
                        raise ProviderError("stream", "Anthropic 返回了不支持的内容块类型。", False)
                    continue

                if event_type == "content_block_delta":
                    index = int(_field(event, "index", -1))
                    state = blocks.get(index)
                    if state is None:
                        raise ProviderError(
                            "stream",
                            "Anthropic 内容增量缺少对应的开始事件。",
                            False,
                        )
                    delta = _field(event, "delta")
                    delta_type = str(_field(delta, "type", ""))
                    if isinstance(state, _IgnoredThinkingState):
                        continue
                    if delta_type == "text_delta":
                        if not isinstance(state, _TextState):
                            raise ProviderError(
                                "stream",
                                "Anthropic 内容增量类型与内容块不匹配。",
                                False,
                            )
                        text = _string_field(delta, "text")
                        if text:
                            yield TextDelta(index, text)
                    elif delta_type == "thinking_delta":
                        if not isinstance(state, _ThinkingState):
                            raise ProviderError(
                                "stream",
                                "Anthropic 内容增量类型与内容块不匹配。",
                                False,
                            )
                        thinking = _string_field(delta, "thinking")
                        if thinking:
                            state.text_parts.append(thinking)
                            yield ThinkingDelta(index, thinking)
                    elif delta_type == "signature_delta":
                        if not isinstance(state, _ThinkingState):
                            raise ProviderError(
                                "stream",
                                "Anthropic 内容增量类型与内容块不匹配。",
                                False,
                            )
                        signature = _string_field(delta, "signature")
                        if signature:
                            state.signature_parts.append(signature)
                    elif delta_type == "input_json_delta":
                        if not isinstance(state, _ToolCallState):
                            raise ProviderError(
                                "stream",
                                "Anthropic 内容增量类型与内容块不匹配。",
                                False,
                            )
                        partial_json = _string_field(delta, "partial_json")
                        if partial_json:
                            state.argument_parts.append(partial_json)
                            yield ToolCallDelta(index, partial_json)
                    else:
                        raise ProviderError(
                            "stream",
                            "Anthropic 返回了不支持的内容增量类型。",
                            False,
                        )
                    continue

                if event_type == "content_block_stop":
                    index = int(_field(event, "index", -1))
                    state = blocks.pop(index, None)
                    if state is None:
                        raise ProviderError(
                            "stream",
                            "Anthropic 内容块结束前缺少开始事件。",
                            False,
                        )
                    if isinstance(state, _IgnoredThinkingState | _TextState):
                        continue
                    if isinstance(state, _ThinkingState):
                        text = "".join(state.text_parts)
                        if not text:
                            raise ProviderError(
                                "stream",
                                "Anthropic 返回了空的 Thinking 内容块。",
                                False,
                            )
                        yield ThinkingComplete(
                            index,
                            ThinkingBlock(text, "".join(state.signature_parts)),
                        )
                        continue
                    if isinstance(state, _RedactedThinkingState):
                        try:
                            block = RedactedThinkingBlock(state.data)
                        except ValueError as error:
                            raise ProviderError(
                                "stream",
                                "Anthropic 返回了无效的加密 Thinking 内容块。",
                                False,
                            ) from error
                        yield ThinkingComplete(index, block)
                        continue

                    raw_arguments = "".join(state.argument_parts) or "{}"
                    try:
                        arguments = json.loads(raw_arguments)
                    except (TypeError, ValueError) as error:
                        raise ProviderError(
                            "stream",
                            "Anthropic 返回的工具参数不是有效 JSON。",
                            False,
                        ) from error
                    if not isinstance(arguments, dict):
                        raise ProviderError(
                            "stream",
                            "Anthropic 返回的工具参数必须是 JSON object。",
                            False,
                        )
                    yield ToolCallComplete(
                        index,
                        ToolCallBlock(state.id, state.name, arguments),
                    )
                    continue

                if event_type == "message_delta":
                    if not message_started:
                        raise ProviderError(
                            "stream",
                            "Anthropic 消息增量早于消息开始。",
                            False,
                        )
                    delta = _field(event, "delta")
                    value = _field(delta, "stop_reason", "")
                    provider_reason = "" if value is None else str(value)
                    continue

                if event_type == "message_stop":
                    if not message_started:
                        raise ProviderError(
                            "stream",
                            "Anthropic 消息结束前缺少开始事件。",
                            False,
                        )
                    if blocks:
                        raise ProviderError(
                            "stream",
                            "Anthropic 消息结束时仍有未关闭内容块，请重试。",
                            True,
                        )
                    completed = True
        except ProviderError:
            raise
        except Exception as error:
            raise self._provider_error(error) from error

        if not completed:
            raise ProviderError("stream", "Anthropic 响应流意外结束，请重试。", True)
        yield StreamEnd(self._stop_reason(provider_reason), provider_reason)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.client.close()
