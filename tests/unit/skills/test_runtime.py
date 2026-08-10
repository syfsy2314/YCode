from pathlib import Path

import pytest

from ycode.agent import AgentMode
from ycode.prompt import PromptRuntimeContext, SupplementKind
from ycode.skills import (
    SkillCatalog,
    SkillConfig,
    SkillContextKind,
    SkillExecutionMode,
    SkillInvocationSource,
    SkillLoader,
    SkillRuntime,
    SkillRuntimeError,
    SkillSnapshot,
    SkillValidationEnvironment,
)

TOOLS = frozenset({"read_file", "write_file", "run_command", "grep"})


def _snapshot(
    name: str,
    *,
    visible: frozenset[str] | None = None,
    allowed: frozenset[str] = frozenset(),
    mode: SkillExecutionMode = SkillExecutionMode.SHARED,
) -> SkillSnapshot:
    root = Path("C:/workspace/.ycode/skills") / name
    context = (
        SkillContextKind.CURRENT if mode is SkillExecutionMode.SHARED else SkillContextKind.NONE
    )
    return SkillSnapshot(
        name,
        f"{name} description",
        root,
        root / "SKILL.md",
        f"{name} SOP",
        SkillConfig(mode, context_kind=context, visible_tools=visible, allowed_tools=allowed),
        fingerprint=(name[0] * 64),
    )


def _runtime(tmp_path: Path, store=None) -> tuple[SkillRuntime, PromptRuntimeContext]:
    catalog = SkillCatalog(
        tmp_path,
        SkillLoader(),
        SkillValidationEnvironment(TOOLS, frozenset(), frozenset()),
    )
    prompt = PromptRuntimeContext()
    return SkillRuntime(catalog, prompt, store), prompt


def test_shared_state_commits_only_after_success(tmp_path: Path) -> None:
    runtime, prompt = _runtime(tmp_path)
    scope = runtime.begin_task(AgentMode.AGENT)
    review = _snapshot("review")

    runtime.activate_shared(scope, review)
    assert runtime.active_names == ()
    runtime.commit_task(scope)

    assert runtime.active_names == ("review",)
    instructions = next(
        item
        for item in prompt.session_supplements
        if item.kind is SupplementKind.SKILL_INSTRUCTIONS
    )
    assert "review SOP" in instructions.content


def test_discard_drops_pending_state_and_authorization(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    scope = runtime.begin_task(AgentMode.AGENT)
    review = _snapshot("review", allowed=frozenset({"read_file"}))
    runtime.activate_shared(scope, review)
    runtime.grant_preapproval(scope, review)

    runtime.discard_task(scope)

    assert runtime.active_names == ()
    assert scope.pending_shared == {}
    assert scope.preapproved_tools == set()


def test_visible_tools_union_and_inherit_base(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    scope = runtime.begin_task(AgentMode.AGENT)
    runtime.activate_shared(scope, _snapshot("read", visible=frozenset({"read_file"})))
    runtime.activate_shared(scope, _snapshot("grep", visible=frozenset({"grep"})))

    assert runtime.visible_tools(scope, TOOLS) == frozenset({"read_file", "grep"})

    runtime.activate_shared(scope, _snapshot("inherit"))
    assert runtime.visible_tools(scope, TOOLS) == TOOLS


def test_isolated_branch_uses_own_tools_and_does_not_commit_shared(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    parent = runtime.begin_task(AgentMode.AGENT)
    child = runtime.branch_for_isolated(parent)
    isolated = _snapshot(
        "isolated", visible=frozenset({"read_file"}), mode=SkillExecutionMode.ISOLATED
    )
    nested_shared = _snapshot("nested")
    runtime.enter_call(child, isolated)
    runtime.activate_shared(child, nested_shared)

    assert runtime.visible_tools(child, TOOLS) == frozenset({"read_file"})
    runtime.commit_task(child)
    runtime.commit_task(parent)
    assert runtime.active_names == ()


def test_call_stack_rejects_cycle_and_fourth_level(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    scope = runtime.begin_task(AgentMode.AGENT)
    snapshots = [_snapshot(name) for name in ("one", "two", "three", "four")]
    for snapshot in snapshots[:3]:
        runtime.enter_call(scope, snapshot)

    with pytest.raises(SkillRuntimeError, match="最大嵌套"):
        runtime.enter_call(scope, snapshots[3])
    with pytest.raises(SkillRuntimeError, match="循环"):
        runtime.enter_call(scope, snapshots[0])


def test_automatic_allowed_tools_need_approval_but_explicit_does_not(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    snapshot = _snapshot("review", allowed=frozenset({"read_file"}))

    assert runtime.needs_activation_approval(snapshot, SkillInvocationSource.AUTOMATIC)
    assert runtime.needs_activation_approval(snapshot, SkillInvocationSource.NESTED)
    assert not runtime.needs_activation_approval(snapshot, SkillInvocationSource.EXPLICIT)


@pytest.mark.asyncio
async def test_deactivate_persists_before_changing_memory(tmp_path: Path) -> None:
    class Store:
        active_session_id = "session"

        def __init__(self) -> None:
            self.calls = []

        async def append_skill_state(self, names):
            self.calls.append(tuple(names))

    store = Store()
    runtime, _ = _runtime(tmp_path, store)
    scope = runtime.begin_task(AgentMode.AGENT)
    runtime.activate_shared(scope, _snapshot("review"))
    runtime.commit_task(scope)

    assert await runtime.deactivate("review")
    assert store.calls == [()]
    assert runtime.active_names == ()


@pytest.mark.asyncio
async def test_deactivate_storage_failure_keeps_active_state(tmp_path: Path) -> None:
    class Store:
        active_session_id = "session"

        async def append_skill_state(self, names):
            raise OSError("failed")

    runtime, _ = _runtime(tmp_path, Store())
    scope = runtime.begin_task(AgentMode.AGENT)
    runtime.activate_shared(scope, _snapshot("review"))
    runtime.commit_task(scope)

    with pytest.raises(OSError):
        await runtime.deactivate("review")
    assert runtime.active_names == ("review",)
