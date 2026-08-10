from pathlib import Path

import pytest

from ycode.agent import AgentMode
from ycode.skills import SkillCallResult, SkillExecutionMode, SkillInvocationSource, SkillTaskScope
from ycode.tools import ToolContext
from ycode.tools.builtin.install_skill import InstallSkillArguments, InstallSkillTool
from ycode.tools.builtin.load_skill import LoadSkillArguments, LoadSkillTool
from ycode.tools.errors import ToolError


class Runtime:
    def __init__(self, result: SkillCallResult) -> None:
        self.result = result
        self.calls = []

    async def invoke(self, name, arguments, source, scope):
        self.calls.append((name, arguments, source, scope))
        return self.result


def test_install_skill_description_tells_model_to_call_before_text_confirmation() -> None:
    definition = InstallSkillTool.definition

    assert "Call this tool when" in definition.description
    assert "Do not ask for confirmation in text" in definition.description
    assert "automatically triggers the required user approval" in definition.description
    assert "skills.sh Skill page" in str(
        definition.input_schema["properties"]["source_url"]["description"]  # type: ignore[index]
    )


@pytest.mark.asyncio
async def test_load_skill_returns_shared_activation_result() -> None:
    runtime = Runtime(SkillCallResult("review", SkillExecutionMode.SHARED, True))
    tool = LoadSkillTool(runtime)  # type: ignore[arg-type]
    scope = SkillTaskScope(AgentMode.AGENT)

    result = await tool.execute(
        LoadSkillArguments(name="review", arguments="Current Changes"),
        ToolContext(Path.cwd(), skill_scope=scope),
    )

    assert "activated" in result.content
    assert runtime.calls == [("review", "Current Changes", SkillInvocationSource.AUTOMATIC, scope)]


@pytest.mark.asyncio
async def test_load_skill_returns_only_isolated_handoff() -> None:
    runtime = Runtime(
        SkillCallResult("review", SkillExecutionMode.ISOLATED, False, "Review complete")
    )
    result = await LoadSkillTool(runtime).execute(  # type: ignore[arg-type]
        LoadSkillArguments(name="review"),
        ToolContext(Path.cwd(), skill_scope=SkillTaskScope(AgentMode.AGENT)),
    )

    assert result.content == "Review complete"
    assert result.metadata["mode"] == "isolated"


@pytest.mark.asyncio
async def test_load_skill_requires_task_scope() -> None:
    runtime = Runtime(SkillCallResult("review", SkillExecutionMode.SHARED, True))

    with pytest.raises(ToolError) as caught:
        await LoadSkillTool(runtime).execute(  # type: ignore[arg-type]
            LoadSkillArguments(name="review"), ToolContext(Path.cwd())
        )

    assert caught.value.code == "skill_context_missing"


@pytest.mark.asyncio
async def test_nested_load_uses_nested_source() -> None:
    runtime = Runtime(SkillCallResult("child", SkillExecutionMode.SHARED, True))
    scope = SkillTaskScope(AgentMode.AGENT)
    scope.call_stack.append(object())  # type: ignore[arg-type]

    await LoadSkillTool(runtime).execute(  # type: ignore[arg-type]
        LoadSkillArguments(name="child"),
        ToolContext(Path.cwd(), skill_scope=scope),
    )

    assert runtime.calls[0][2] is SkillInvocationSource.NESTED


@pytest.mark.asyncio
async def test_install_skill_reports_available_and_unavailable_results() -> None:
    from ycode.skills.models import (
        SkillCatalogEntry,
        SkillProblem,
        SkillProblemSeverity,
        SkillSnapshot,
    )

    root = Path.cwd() / "review"
    available = SkillCatalogEntry(
        "review",
        root / "SKILL.md",
        SkillSnapshot("review", "Review", root, root / "SKILL.md", "Do it"),
    )
    unavailable = SkillCatalogEntry(
        "broken",
        root / "SKILL.md",
        None,
        (SkillProblem("missing", "Missing tool", SkillProblemSeverity.ERROR),),
    )

    class Installer:
        def __init__(self):
            self.results = [available, unavailable]

        async def install(self, url):
            return self.results.pop(0)

    tool = InstallSkillTool(Installer())  # type: ignore[arg-type]
    first = await tool.execute(
        InstallSkillArguments(source_url="https://example.com/review.zip"),
        ToolContext(Path.cwd()),
    )
    second = await tool.execute(
        InstallSkillArguments(source_url="https://example.com/broken.zip"),
        ToolContext(Path.cwd()),
    )

    assert first.metadata["status"] == "installed"
    assert second.metadata["status"] == "unavailable"
