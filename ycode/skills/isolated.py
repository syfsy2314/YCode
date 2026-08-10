"""隔离 Skill 的临时 Agent 装配与执行。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, cast

from ycode.agent import AgentMode, AgentTermination, ConversationRunner
from ycode.config.models import ProviderConfig, ProviderProtocol
from ycode.context.models import ConversationMemory
from ycode.core.messages import ChatMessage
from ycode.core.provider import AgentChatProvider
from ycode.prompt import PromptRuntimeContext, SupplementKind, SupplementScope, SystemSupplement
from ycode.skills.context import SkillContextBuilder
from ycode.skills.models import SkillSnapshot, SkillTaskScope


class IsolatedLoopFactory(Protocol):
    def __call__(
        self,
        provider: AgentChatProvider,
        prompt_runtime: PromptRuntimeContext,
        scope: SkillTaskScope,
    ) -> ConversationRunner: ...


class IsolatedSkillRunnerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ScopedSkillConversationRunner:
    def __init__(self, runner: ConversationRunner, scope: SkillTaskScope) -> None:
        self._runner = runner
        self._scope = scope
        self.supported_modes = runner.supported_modes

    def start_turn(self, history, user_message, mode):
        start = getattr(self._runner, "start_turn_with_skill_scope", None)
        if not callable(start):
            raise RuntimeError("隔离 Agent 不支持 Skill 分支作用域")
        return start(history, user_message, mode, self._scope)

    async def close(self) -> None:
        await self._runner.close()


class IsolatedSkillRunner:
    def __init__(
        self,
        current_provider: ProviderConfig,
        named_provider_loader: Callable[[str], ProviderConfig],
        provider_factory: Callable[[ProviderConfig], AgentChatProvider],
        loop_factory: IsolatedLoopFactory,
        context_builder: SkillContextBuilder,
        history_provider: Callable[[], Sequence[ChatMessage]],
        memory_provider: Callable[[], ConversationMemory | None],
        mode_provider: Callable[[], AgentMode],
    ) -> None:
        if current_provider.protocol is not ProviderProtocol.ANTHROPIC:
            raise ValueError("隔离 Skill 当前 Provider 必须使用 Anthropic 协议")
        self._current_provider = current_provider
        self._named_provider_loader = named_provider_loader
        self._provider_factory = provider_factory
        self._loop_factory = loop_factory
        self._context_builder = context_builder
        self._history_provider = history_provider
        self._memory_provider = memory_provider
        self._mode_provider = mode_provider
        self._active_turn = None

    async def run(
        self,
        snapshot: SkillSnapshot,
        scope: SkillTaskScope,
        arguments: str | None,
    ) -> str:
        config = self._select_provider(snapshot)
        user_task = ChatMessage.user_text(_expanded_task(snapshot.name, arguments))
        context = await self._context_builder.build(
            snapshot.config.context_kind,
            self._history_provider(),
            self._memory_provider(),
            user_task,
            recent_turns=snapshot.config.recent_turns,
        )
        prompt_runtime = PromptRuntimeContext()
        prompt_runtime.set_skill_instructions(((snapshot.name, snapshot.instructions),))
        if context.summary is not None:
            prompt_runtime.set_session_supplement(
                SystemSupplement(
                    SupplementKind.MEMORY,
                    context.summary.summary,
                    SupplementScope.SESSION,
                )
            )

        provider = self._provider_factory(config)
        runner = self._loop_factory(provider, prompt_runtime, scope)
        turn = runner.start_turn(context.history, context.user_task, self._mode_provider())
        self._active_turn = turn
        try:
            async for _ in turn:
                pass
            result = turn.result
            if result is None:
                raise IsolatedSkillRunnerError(
                    "isolated_missing_result", "隔离 Skill 结束时缺少结果。"
                )
            if result.termination is not AgentTermination.COMPLETED:
                raise IsolatedSkillRunnerError(
                    "isolated_failed",
                    result.error_message or "隔离 Skill 执行失败。",
                )
            assert result.final_message is not None
            handoff = result.final_message.text.strip()
            if not handoff:
                raise IsolatedSkillRunnerError(
                    "isolated_empty_handoff", "隔离 Skill 未返回最终交接文本。"
                )
            return handoff
        finally:
            self._active_turn = None
            await runner.close()

    def cancel(self) -> None:
        if self._active_turn is not None:
            self._active_turn.cancel()

    def _select_provider(self, snapshot: SkillSnapshot) -> ProviderConfig:
        config = (
            self._current_provider
            if snapshot.config.model_name is None
            else self._named_provider_loader(snapshot.config.model_name)
        )
        if config.protocol is not ProviderProtocol.ANTHROPIC:
            raise IsolatedSkillRunnerError(
                "isolated_provider_invalid", "隔离 Skill 只能使用 Anthropic Provider。"
            )
        return cast(ProviderConfig, config)


def _expanded_task(name: str, arguments: str | None) -> str:
    detail = (
        f"Invocation arguments:\n{arguments}"
        if arguments is not None and arguments.strip()
        else "No arguments were provided."
    )
    return f'Use the "{name}" skill for this task.\n\n{detail}'


__all__ = [
    "IsolatedSkillRunner",
    "IsolatedSkillRunnerError",
    "ScopedSkillConversationRunner",
]
