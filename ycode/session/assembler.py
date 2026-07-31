"""把语义流事件组装为不可变 Assistant 消息。"""

import json
from dataclasses import dataclass, field

from ycode.core.events import (
    StopReason,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    TokenUsage,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)
from ycode.core.messages import (
    ChatMessage,
    ContentBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    thaw_json,
)
from ycode.errors import MessageAssemblyError


@dataclass(slots=True)
class _TextState:
    text_parts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ThinkingState:
    text_parts: list[str] = field(default_factory=list)
    completed_block: ThinkingBlock | RedactedThinkingBlock | None = None


@dataclass(slots=True)
class _ToolCallState:
    id: str
    name: str
    argument_parts: list[str] = field(default_factory=list)
    completed_block: ToolCallBlock | None = None


type _ResponseState = _TextState | _ThinkingState | _ToolCallState


class ResponseAssembler:
    def __init__(self) -> None:
        self._states: dict[int, _ResponseState] = {}
        self._stream_ended = False
        self._finished = False
        self._stop_reason: StopReason | None = None
        self._provider_reason = ""
        self._usage = TokenUsage()

    @property
    def stop_reason(self) -> StopReason | None:
        return self._stop_reason

    @property
    def provider_reason(self) -> str:
        return self._provider_reason

    @property
    def usage(self) -> TokenUsage:
        return self._usage

    def consume(self, event: StreamEvent) -> None:
        if self._finished:
            raise MessageAssemblyError("响应已经完成组装")
        if self._stream_ended:
            raise MessageAssemblyError("响应结束后收到额外事件")

        if isinstance(event, TextDelta):
            self._consume_text(event)
        elif isinstance(event, ThinkingDelta):
            self._consume_thinking_delta(event)
        elif isinstance(event, ThinkingComplete):
            self._consume_thinking_complete(event)
        elif isinstance(event, ToolCallStart):
            self._consume_tool_start(event)
        elif isinstance(event, ToolCallDelta):
            self._consume_tool_delta(event)
        elif isinstance(event, ToolCallComplete):
            self._consume_tool_complete(event)
        elif isinstance(event, StreamEnd):
            self._consume_stream_end(event)
        else:  # pragma: no cover - 类型联合的防御分支
            raise MessageAssemblyError("收到未知流事件")

    def finish(self) -> ChatMessage:
        if self._finished:
            raise MessageAssemblyError("响应已经完成组装")
        if not self._stream_ended:
            raise MessageAssemblyError("响应流缺少结束事件")

        content: list[tuple[int, ContentBlock]] = []
        for index, state in self._states.items():
            if isinstance(state, _TextState):
                content.append((index, TextBlock("".join(state.text_parts))))
            elif isinstance(state, _ThinkingState):
                if state.completed_block is None:  # pragma: no cover - StreamEnd 已校验
                    raise MessageAssemblyError("响应包含未完成的 Thinking 块")
                content.append((index, state.completed_block))
            else:
                if state.completed_block is None:  # pragma: no cover - StreamEnd 已校验
                    raise MessageAssemblyError("响应包含未完成的工具调用")
                content.append((index, state.completed_block))

        self._finished = True
        ordered = tuple(block for _, block in sorted(content, key=lambda item: item[0]))
        return ChatMessage(role="assistant", content=ordered)

    def _consume_text(self, event: TextDelta) -> None:
        state = self._states.get(event.index)
        if state is None:
            state = _TextState()
            self._states[event.index] = state
        if not isinstance(state, _TextState):
            raise MessageAssemblyError(f"内容块 {event.index} 的增量类型不匹配")
        state.text_parts.append(event.text)

    def _consume_thinking_delta(self, event: ThinkingDelta) -> None:
        state = self._states.get(event.index)
        if state is None:
            state = _ThinkingState()
            self._states[event.index] = state
        if not isinstance(state, _ThinkingState):
            raise MessageAssemblyError(f"内容块 {event.index} 的增量类型不匹配")
        if state.completed_block is not None:
            raise MessageAssemblyError(f"内容块 {event.index} 已经完成")
        state.text_parts.append(event.text)

    def _consume_thinking_complete(self, event: ThinkingComplete) -> None:
        state = self._states.get(event.index)
        if state is None:
            if isinstance(event.block, RedactedThinkingBlock):
                self._states[event.index] = _ThinkingState(completed_block=event.block)
                return
            raise MessageAssemblyError(f"内容块 {event.index} 完成前缺少 Thinking 增量")
        if not isinstance(state, _ThinkingState):
            raise MessageAssemblyError(f"内容块 {event.index} 的完成类型不匹配")
        if state.completed_block is not None:
            raise MessageAssemblyError(f"内容块 {event.index} 重复完成")
        if not isinstance(event.block, ThinkingBlock):
            raise MessageAssemblyError(f"内容块 {event.index} 的完成类型不匹配")
        if "".join(state.text_parts) != event.block.text:
            raise MessageAssemblyError(f"内容块 {event.index} 的 Thinking 完成内容不一致")
        state.completed_block = event.block

    def _consume_tool_start(self, event: ToolCallStart) -> None:
        if event.index in self._states:
            raise MessageAssemblyError(f"内容块 {event.index} 重复开始")
        self._states[event.index] = _ToolCallState(id=event.id, name=event.name)

    def _consume_tool_delta(self, event: ToolCallDelta) -> None:
        state = self._states.get(event.index)
        if not isinstance(state, _ToolCallState):
            raise MessageAssemblyError(f"内容块 {event.index} 的工具增量缺少开始事件")
        if state.completed_block is not None:
            raise MessageAssemblyError(f"内容块 {event.index} 已经完成")
        state.argument_parts.append(event.arguments_delta)

    def _consume_tool_complete(self, event: ToolCallComplete) -> None:
        state = self._states.get(event.index)
        if not isinstance(state, _ToolCallState):
            raise MessageAssemblyError(f"内容块 {event.index} 的工具完成缺少开始事件")
        if state.completed_block is not None:
            raise MessageAssemblyError(f"内容块 {event.index} 重复完成")

        raw_arguments = "".join(state.argument_parts) or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, ValueError) as error:
            raise MessageAssemblyError(f"内容块 {event.index} 的工具参数不是有效 JSON") from error
        if not isinstance(arguments, dict):
            raise MessageAssemblyError(f"内容块 {event.index} 的工具参数必须是 JSON object")
        if (
            event.block.id != state.id
            or event.block.name != state.name
            or thaw_json(event.block.arguments) != arguments
        ):
            raise MessageAssemblyError(f"内容块 {event.index} 的工具完成内容不一致")
        state.completed_block = event.block

    def _consume_stream_end(self, event: StreamEnd) -> None:
        if not self._states:
            raise MessageAssemblyError("Assistant 响应没有内容块")
        for index, state in self._states.items():
            if isinstance(state, _ThinkingState) and state.completed_block is None:
                raise MessageAssemblyError(f"内容块 {index} 的 Thinking 尚未完成")
            if isinstance(state, _ToolCallState) and state.completed_block is None:
                raise MessageAssemblyError(f"内容块 {index} 的工具调用尚未完成")
        self._stream_ended = True
        self._stop_reason = event.stop_reason
        self._provider_reason = event.provider_reason
        self._usage = event.usage
