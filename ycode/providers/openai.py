"""OpenAI Chat Completions API 的结构化流式适配器。"""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import AsyncOpenAI

from ycode.config.models import ProviderConfig
from ycode.core.events import (
    StopReason,
    StreamEnd,
    StreamEvent,
    TextDelta,
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


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


@dataclass(slots=True)
class _ToolCallState:
    id_parts: list[str] = field(default_factory=list)
    name_parts: list[str] = field(default_factory=list)
    argument_parts: list[str] = field(default_factory=list)


class OpenAIProvider:
    def __init__(self, config: ProviderConfig, *, client: Any | None = None) -> None:
        self._config = config
        self.client = client or AsyncOpenAI(
            api_key=config.api_key.get_secret_value(),
            base_url=config.base_url,
            max_retries=0,
        )
        self._closed = False

    @staticmethod
    def _messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "user":
                saw_text = False
                text_parts: list[str] = []
                for block in message.content:
                    if isinstance(block, TextBlock):
                        saw_text = True
                        text_parts.append(block.text)
                    elif isinstance(block, ToolResultBlock):
                        if saw_text:
                            raise ProviderError(
                                "request",
                                "OpenAI 工具结果必须位于用户文本之前。",
                                False,
                            )
                        result.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.tool_call_id,
                                "content": block.content,
                            }
                        )
                if text_parts:
                    result.append({"role": "user", "content": "".join(text_parts)})
                continue

            text_parts = []
            tool_calls: list[dict[str, Any]] = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolCallBlock):
                    tool_calls.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(
                                    thaw_json(block.arguments),
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    )
                elif isinstance(block, ThinkingBlock | RedactedThinkingBlock):
                    raise ProviderError(
                        "request",
                        "OpenAI 协议无法转换 Anthropic Thinking 历史。",
                        False,
                    )
            value: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(text_parts) if text_parts else None,
            }
            if tool_calls:
                value["tool_calls"] = tool_calls
            result.append(value)
        return result

    @staticmethod
    def _stop_reason(reason: str | None) -> StopReason:
        return {
            "stop": StopReason.END_TURN,
            "tool_calls": StopReason.TOOL_USE,
            "function_call": StopReason.TOOL_USE,
            "length": StopReason.MAX_TOKENS,
            "content_filter": StopReason.CONTENT_FILTER,
        }.get(reason or "", StopReason.UNKNOWN)

    @staticmethod
    def _provider_error(error: Exception) -> ProviderError:
        if isinstance(error, openai.AuthenticationError):
            return ProviderError("authentication", "OpenAI API 认证失败，请检查 API Key。", False)
        if isinstance(error, openai.RateLimitError):
            return ProviderError("rate_limit", "OpenAI API 请求过于频繁，请稍后重试。", True)
        if isinstance(error, openai.APITimeoutError):
            return ProviderError("network", "连接 OpenAI API 超时，请重试。", True)
        if isinstance(error, openai.APIConnectionError):
            return ProviderError("network", "无法连接 OpenAI API，请检查网络和地址。", True)
        if isinstance(error, openai.APIStatusError):
            if error.status_code >= 500:
                return ProviderError("server", "OpenAI 服务暂时不可用，请稍后重试。", True)
            return ProviderError("request", "OpenAI 拒绝了请求，请检查模型和配置。", False)
        return ProviderError("stream", "OpenAI 响应流意外中断，请重试。", True)

    @staticmethod
    def _effective_choice(choice: object) -> bool:
        delta = _field(choice, "delta")
        content = _field(delta, "content")
        tool_calls = _field(delta, "tool_calls", [])
        return bool(content or tool_calls or _field(choice, "finish_reason"))

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamEvent]:
        completed = False
        provider_reason = ""
        selected_choice: int | None = None
        tool_states: dict[int, _ToolCallState] = {}
        try:
            stream = await self.client.chat.completions.create(
                model=self._config.model,
                messages=self._messages(messages),
                stream=True,
            )
            async for chunk in stream:
                choices = [
                    choice
                    for choice in (_field(chunk, "choices", []) or [])
                    if self._effective_choice(choice)
                ]
                if len(choices) > 1:
                    raise ProviderError("stream", "OpenAI 返回了多个有效 choice，无法合并。", False)
                if not choices:
                    continue
                choice = choices[0]
                choice_index = int(_field(choice, "index", 0))
                if selected_choice is None:
                    selected_choice = choice_index
                elif selected_choice != choice_index:
                    raise ProviderError("stream", "OpenAI 返回了多个有效 choice，无法合并。", False)
                if completed:
                    raise ProviderError("stream", "OpenAI 完成后返回了额外内容。", True)

                delta = _field(choice, "delta")
                content = _field(delta, "content")
                if isinstance(content, str) and content:
                    yield TextDelta(0, content)

                for tool_call in _field(delta, "tool_calls", []) or []:
                    tool_index = int(_field(tool_call, "index", 0))
                    block_index = tool_index + 1
                    state = tool_states.setdefault(block_index, _ToolCallState())
                    tool_id = _field(tool_call, "id", "")
                    if isinstance(tool_id, str) and tool_id:
                        state.id_parts.append(tool_id)
                    function = _field(tool_call, "function")
                    name = _field(function, "name", "")
                    arguments = _field(function, "arguments", "")
                    if isinstance(name, str) and name:
                        state.name_parts.append(name)
                    if isinstance(arguments, str) and arguments:
                        state.argument_parts.append(arguments)

                finish_reason = _field(choice, "finish_reason")
                if finish_reason is not None:
                    provider_reason = str(finish_reason)
                    for index, state in sorted(tool_states.items()):
                        tool_id = "".join(state.id_parts)
                        name = "".join(state.name_parts)
                        if not tool_id or not name:
                            raise ProviderError(
                                "stream",
                                "OpenAI 返回了无效的工具调用标识。",
                                False,
                            )
                        raw_arguments = "".join(state.argument_parts) or "{}"
                        try:
                            arguments = json.loads(raw_arguments)
                        except (TypeError, ValueError) as error:
                            raise ProviderError(
                                "stream",
                                "OpenAI 返回的工具参数不是有效 JSON。",
                                False,
                            ) from error
                        if not isinstance(arguments, dict):
                            raise ProviderError(
                                "stream",
                                "OpenAI 返回的工具参数必须是 JSON object。",
                                False,
                            )
                        yield ToolCallStart(index, tool_id, name)
                        for part in state.argument_parts:
                            yield ToolCallDelta(index, part)
                        yield ToolCallComplete(
                            index,
                            ToolCallBlock(tool_id, name, arguments),
                        )
                    completed = True
        except ProviderError:
            raise
        except Exception as error:
            raise self._provider_error(error) from error

        if not completed:
            raise ProviderError("stream", "OpenAI 响应流意外结束，请重试。", True)
        yield StreamEnd(self._stop_reason(provider_reason), provider_reason)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.client.close()
