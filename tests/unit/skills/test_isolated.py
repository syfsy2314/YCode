from pathlib import Path

import pytest
from pydantic import SecretStr

from ycode.agent import (
    AgentMode,
    AgentTermination,
    AgentTurnResult,
    AgentTurnStream,
    FinalResponseEvent,
)
from ycode.config import ProviderConfig, ProviderProtocol
from ycode.context import ConversationMemory
from ycode.core.messages import ChatMessage
from ycode.prompt import SupplementKind
from ycode.skills import (
    SkillConfig,
    SkillContextBuilder,
    SkillContextKind,
    SkillExecutionMode,
    SkillSnapshot,
)
from ycode.skills.isolated import IsolatedSkillRunner, IsolatedSkillRunnerError
from ycode.skills.models import SkillTaskScope


def provider_config(name: str, protocol: ProviderProtocol = ProviderProtocol.ANTHROPIC):
    return ProviderConfig(
        name=name,
        protocol=protocol,
        model=f"{name}-model",
        base_url="https://example.com",
        api_key=SecretStr("secret"),
    )


def snapshot(
    *, model_name: str | None = None, context: SkillContextKind = SkillContextKind.NONE
) -> SkillSnapshot:
    root = Path("C:/workspace/.ycode/skills/review")
    return SkillSnapshot(
        "review",
        "Review files",
        root,
        root / "SKILL.md",
        "Review carefully.",
        SkillConfig(
            SkillExecutionMode.ISOLATED,
            model_name=model_name,
            context_kind=context,
            recent_turns=1 if context is SkillContextKind.RECENT else None,
        ),
        fingerprint="a" * 64,
    )


class Compactor:
    async def compact(self, source):
        from ycode.context import SummaryResult

        return SummaryResult(ConversationMemory("temporary summary"), source.messages)


class FakeProvider:
    async def close(self):
        pass


class FakeRunner:
    supported_modes = frozenset({AgentMode.AGENT, AgentMode.PLAN_ONLY})

    def __init__(self, final_text: str = "handoff", termination=AgentTermination.COMPLETED):
        self.final_text = final_text
        self.termination = termination
        self.closed = False
        self.started = None

    def start_turn(self, history, user_message, mode):
        self.started = (tuple(history), user_message, mode)

        async def produce(turn):
            if self.termination is AgentTermination.COMPLETED:
                final = ChatMessage.assistant_text(self.final_text)
                turn.complete(
                    AgentTurnResult(
                        AgentTermination.COMPLETED,
                        (user_message, final),
                        final,
                    )
                )
                yield FinalResponseEvent(final)
            else:
                turn.complete(
                    AgentTurnResult(
                        self.termination,
                        (user_message,),
                        error_code=(
                            "fake_error" if self.termination is AgentTermination.ERROR else ""
                        ),
                        error_message="failed",
                    )
                )
                if False:
                    yield

        return AgentTurnStream(produce)

    async def close(self):
        self.closed = True


def make_runner(tmp_path, *, named=None, fake_runner=None, history=(), memory=None):
    selected = []
    prompts = []
    loop = fake_runner or FakeRunner()

    def provider_factory(config):
        selected.append(config.name)
        return FakeProvider()

    def loop_factory(provider, prompt_runtime, scope):
        prompts.append(prompt_runtime)
        return loop

    runner = IsolatedSkillRunner(
        provider_config("current"),
        lambda name: named or provider_config(name),
        provider_factory,
        loop_factory,
        SkillContextBuilder(Compactor()),
        lambda: history,
        lambda: memory,
        lambda: AgentMode.AGENT,
    )
    return runner, selected, prompts, loop


@pytest.mark.asyncio
async def test_uses_current_or_named_anthropic_provider_and_own_prompt(tmp_path: Path) -> None:
    runner, selected, prompts, loop = make_runner(tmp_path)
    scope = SkillTaskScope(AgentMode.AGENT)

    assert await runner.run(snapshot(), scope, "check parser") == "handoff"
    assert selected == ["current"]
    assert loop.started[1].text.endswith("Invocation arguments:\ncheck parser")
    instructions = next(
        item
        for item in prompts[0].session_supplements
        if item.kind is SupplementKind.SKILL_INSTRUCTIONS
    )
    assert "Review carefully." in instructions.content

    runner, selected, _, _ = make_runner(tmp_path, named=provider_config("fast"))
    await runner.run(snapshot(model_name="fast"), SkillTaskScope(AgentMode.AGENT), None)
    assert selected == ["fast"]


@pytest.mark.asyncio
async def test_summary_context_is_temporary_and_only_handoff_is_returned(tmp_path: Path) -> None:
    history = (ChatMessage.user_text("old"), ChatMessage.assistant_text("answer"))
    runner, _, prompts, loop = make_runner(tmp_path, history=history)

    result = await runner.run(
        snapshot(context=SkillContextKind.SUMMARY),
        SkillTaskScope(AgentMode.AGENT),
        None,
    )

    assert result == "handoff"
    assert loop.started[0] == ()
    assert any(item.kind is SupplementKind.MEMORY for item in prompts[0].session_supplements)
    assert loop.closed


@pytest.mark.asyncio
async def test_rejects_openai_named_provider_before_loop_creation(tmp_path: Path) -> None:
    runner, selected, _, _ = make_runner(
        tmp_path,
        named=provider_config("open", ProviderProtocol.OPENAI),
    )

    with pytest.raises(IsolatedSkillRunnerError, match="Anthropic"):
        await runner.run(snapshot(model_name="open"), SkillTaskScope(AgentMode.AGENT), None)
    assert selected == []


@pytest.mark.asyncio
async def test_failure_closes_runner_without_clearing_parent_authorization(
    tmp_path: Path,
) -> None:
    loop = FakeRunner(termination=AgentTermination.ERROR)
    runner, _, _, loop = make_runner(tmp_path, fake_runner=loop)
    scope = SkillTaskScope(AgentMode.AGENT)
    scope.preapproved_tools.add("read_file")

    with pytest.raises(IsolatedSkillRunnerError, match="failed"):
        await runner.run(snapshot(), scope, None)

    assert loop.closed
    assert scope.preapproved_tools == {"read_file"}
