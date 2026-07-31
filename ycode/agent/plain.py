"""不使用工具的单次 Provider 纯聊天运行器。"""

import asyncio
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
    AgentTextDelta,
    AgentThinkingDelta,
    FinalResponseEvent,
)
from ycode.core.events import TextDelta, ThinkingDelta
from ycode.core.messages import ChatMessage
from ycode.core.provider import ChatProvider
from ycode.errors import MessageAssemblyError, ProviderError
from ycode.session.assembler import ResponseAssembler


class PlainChatRunner:
    """保持未接入工具 Provider 的现有单次聊天行为。"""

    supported_modes = frozenset({AgentMode.AGENT})

    def __init__(self, provider: ChatProvider) -> None:
        self._provider = provider

    def start_turn(
        self,
        history: Sequence[ChatMessage],
        user_message: ChatMessage,
        mode: AgentMode,
    ) -> AgentTurn:
        if mode not in self.supported_modes:
            raise ValueError("纯聊天运行器不支持当前模式")
        return AgentTurnStream(lambda turn: self._run(turn, tuple(history), user_message))

    async def close(self) -> None:
        await self._provider.close()

    async def _run(
        self,
        turn: AgentTurnStream,
        history: tuple[ChatMessage, ...],
        user_message: ChatMessage,
    ) -> AsyncIterator[AgentEvent]:
        assembler = ResponseAssembler()
        try:
            stream = self._provider.stream_chat((*history, user_message))
            while True:
                try:
                    event = await turn.run_child(anext(stream))
                except StopAsyncIteration:
                    break
                assembler.consume(event)
                if isinstance(event, ThinkingDelta):
                    yield AgentThinkingDelta(1, event.index, event.text)
                elif isinstance(event, TextDelta):
                    yield AgentTextDelta(1, event.index, event.text)

            assistant_message = assembler.finish()
            turn.complete(
                AgentTurnResult(
                    termination=AgentTermination.COMPLETED,
                    messages=(user_message, assistant_message),
                    final_message=assistant_message,
                    usage=assembler.usage,
                )
            )
            yield FinalResponseEvent(assistant_message)
        except asyncio.CancelledError:
            if not turn.cancellation_requested:
                raise
            message = "当前对话已取消。"
            turn.complete(
                AgentTurnResult(
                    termination=AgentTermination.CANCELLED,
                    messages=(user_message,),
                    error_message=message,
                )
            )
            yield AgentCancelledEvent(message)
        except ProviderError as error:
            turn.complete(
                AgentTurnResult(
                    termination=AgentTermination.ERROR,
                    messages=(user_message,),
                    error_code=error.code,
                    error_message=error.user_message,
                )
            )
            yield AgentErrorEvent(error.code, error.user_message)
        except MessageAssemblyError:
            message = "模型响应结构无效，请重试。"
            turn.complete(
                AgentTurnResult(
                    termination=AgentTermination.ERROR,
                    messages=(user_message,),
                    error_code="invalid_response",
                    error_message=message,
                )
            )
            yield AgentErrorEvent("invalid_response", message)
