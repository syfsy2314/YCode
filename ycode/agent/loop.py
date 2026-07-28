"""最小 ReAct Agent Loop。"""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

from ycode.agent.contracts import (
    AgentMode,
    AgentTermination,
    AgentTurn,
    AgentTurnResult,
    AgentTurnStream,
)
from ycode.agent.events import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentEvent,
    AgentLimitReachedEvent,
    AgentTextDelta,
    AgentThinkingDelta,
    FinalResponseEvent,
    ToolExecutionCancelled,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from ycode.agent.prompt import SystemPromptBuilder
from ycode.core.events import StopReason, TextDelta, ThinkingDelta
from ycode.core.messages import (
    ChatMessage,
    ToolCallBlock,
    ToolResultBlock,
    thaw_json,
)
from ycode.core.provider import AgentChatProvider
from ycode.errors import MessageAssemblyError, ProviderError
from ycode.session.assembler import ResponseAssembler
from ycode.tools.contracts import ToolAccess, ToolContext, ToolExecutionRecord
from ycode.tools.registry import ToolRegistry
from ycode.tools.scheduler import (
    ScheduledToolCancelled,
    ScheduledToolCompleted,
    ScheduledToolStarted,
    ToolScheduler,
)


class AgentLoop:
    supported_modes = frozenset({AgentMode.AGENT, AgentMode.PLAN_ONLY})

    def __init__(
        self,
        provider: AgentChatProvider,
        registry: ToolRegistry,
        scheduler: ToolScheduler,
        prompt_builder: SystemPromptBuilder,
        context: ToolContext,
        *,
        max_rounds: int = 10,
    ) -> None:
        if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 1:
            raise ValueError("Agent 最大轮数必须是正整数")
        self._provider = provider
        self._registry = registry
        self._scheduler = scheduler
        self._prompt_builder = prompt_builder
        self._context = context
        self._max_rounds = max_rounds

    def start_turn(
        self,
        history: Sequence[ChatMessage],
        user_message: ChatMessage,
        mode: AgentMode,
    ) -> AgentTurn:
        history_snapshot = tuple(history)
        if user_message.role != "user":
            raise ValueError("Agent 回合必须使用用户消息")
        if mode not in self.supported_modes:
            raise ValueError("AgentLoop 不支持当前模式")
        return AgentTurnStream(lambda turn: self._run(turn, history_snapshot, user_message, mode))

    async def close(self) -> None:
        await self._provider.close()

    async def _run(
        self,
        turn: AgentTurnStream,
        history: tuple[ChatMessage, ...],
        user_message: ChatMessage,
        mode: AgentMode,
    ) -> AsyncIterator[AgentEvent]:
        working_messages = [*history, user_message]
        turn_messages = [user_message]
        allowed_access = (
            frozenset({ToolAccess.READ})
            if mode is AgentMode.PLAN_ONLY
            else frozenset({ToolAccess.READ, ToolAccess.WRITE})
        )
        definitions = self._registry.definitions(allowed_access)
        system_prompt = self._prompt_builder.build(mode, definitions)

        try:
            for round_number in range(1, self._max_rounds + 1):
                assembler = ResponseAssembler()
                stream = self._provider.stream_chat(
                    working_messages,
                    system_prompt=system_prompt,
                    tools=definitions,
                )
                while True:
                    try:
                        event = await turn.run_child(anext(stream))
                    except StopAsyncIteration:
                        break
                    assembler.consume(event)
                    if isinstance(event, ThinkingDelta):
                        yield AgentThinkingDelta(
                            round_number,
                            event.index,
                            event.text,
                        )
                    elif isinstance(event, TextDelta):
                        yield AgentTextDelta(
                            round_number,
                            event.index,
                            event.text,
                        )

                assistant_message = assembler.finish()
                stop_reason = assembler.stop_reason
                tool_calls = assistant_message.blocks(ToolCallBlock)
                working_messages.append(assistant_message)
                turn_messages.append(assistant_message)

                if stop_reason is StopReason.END_TURN and not tool_calls:
                    turn.complete(
                        AgentTurnResult(
                            termination=AgentTermination.COMPLETED,
                            messages=tuple(turn_messages),
                            final_message=assistant_message,
                        )
                    )
                    yield FinalResponseEvent(assistant_message)
                    return

                if stop_reason is not StopReason.TOOL_USE:
                    code = (
                        "inconsistent_response"
                        if stop_reason is StopReason.END_TURN and tool_calls
                        else "unexpected_stop_reason"
                    )
                    message = (
                        "模型结束原因与工具调用内容不一致。"
                        if code == "inconsistent_response"
                        else f"模型以异常原因停止：{stop_reason or StopReason.UNKNOWN}。"
                    )
                    yield self._finish_error(turn, turn_messages, code, message)
                    return

                if not tool_calls:
                    yield self._finish_error(
                        turn,
                        turn_messages,
                        "missing_tool_calls",
                        "模型声明使用工具，但响应中没有工具调用。",
                    )
                    return

                records: list[ToolExecutionRecord] = []
                scheduled = self._scheduler.stream(
                    tool_calls,
                    self._context,
                    allowed_access,
                )
                while True:
                    try:
                        scheduled_event = await turn.run_child(anext(scheduled))
                    except StopAsyncIteration:
                        break
                    if isinstance(scheduled_event, ScheduledToolStarted):
                        yield ToolExecutionStarted(
                            round_number,
                            scheduled_event.position,
                            scheduled_event.call,
                        )
                    elif isinstance(scheduled_event, ScheduledToolCompleted):
                        records.append(scheduled_event.record)
                        yield ToolExecutionCompleted(round_number, scheduled_event.record)
                    elif isinstance(scheduled_event, ScheduledToolCancelled):
                        yield ToolExecutionCancelled(
                            round_number,
                            scheduled_event.position,
                            scheduled_event.call,
                        )

                if len(records) != len(tool_calls):
                    yield self._finish_error(
                        turn,
                        turn_messages,
                        "incomplete_tool_batch",
                        "工具批次没有产生完整结果。",
                    )
                    return

                records.sort(key=lambda record: record.position)
                result_message = ChatMessage(
                    role="user",
                    content=tuple(
                        ToolResultBlock(
                            record.call.id,
                            _result_content(record),
                            record.result.is_error,
                        )
                        for record in records
                    ),
                )
                working_messages.append(result_message)
                turn_messages.append(result_message)

                if round_number == self._max_rounds:
                    message = f"Agent 已达到最大轮数 {self._max_rounds}。"
                    turn.complete(
                        AgentTurnResult(
                            termination=AgentTermination.LIMIT_REACHED,
                            messages=tuple(turn_messages),
                            error_message=message,
                        )
                    )
                    yield AgentLimitReachedEvent(self._max_rounds, message)
                    return
        except asyncio.CancelledError:
            if not turn.cancellation_requested:
                raise
            message = "当前 Agent 回合已取消。"
            turn.complete(
                AgentTurnResult(
                    termination=AgentTermination.CANCELLED,
                    messages=tuple(turn_messages),
                    error_message=message,
                )
            )
            yield AgentCancelledEvent(message)
        except ProviderError as error:
            yield self._finish_error(
                turn,
                turn_messages,
                error.code,
                error.user_message,
            )
        except MessageAssemblyError:
            yield self._finish_error(
                turn,
                turn_messages,
                "invalid_response",
                "模型响应结构无效，请重试。",
            )
        except Exception:
            yield self._finish_error(
                turn,
                turn_messages,
                "agent_internal_error",
                "Agent 运行时发生内部错误。",
            )

    @staticmethod
    def _finish_error(
        turn: AgentTurnStream,
        messages: list[ChatMessage],
        code: str,
        message: str,
    ) -> AgentErrorEvent:
        turn.complete(
            AgentTurnResult(
                termination=AgentTermination.ERROR,
                messages=tuple(messages),
                error_code=code,
                error_message=message,
            )
        )
        return AgentErrorEvent(code, message)


def _result_content(record: ToolExecutionRecord) -> str:
    return json.dumps(
        {
            "content": record.result.content,
            "metadata": thaw_json(record.result.metadata),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
