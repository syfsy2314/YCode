import pytest

from ycode.prompt import (
    PromptRuntimeContext,
    SupplementKind,
    SupplementScope,
    SystemSupplement,
)


def test_runtime_uses_full_instruction_first_then_compact() -> None:
    runtime = PromptRuntimeContext()

    first = runtime.begin_turn("agent")
    second = runtime.begin_turn("agent")

    assert first.full_mode_instruction
    assert "Current task mode: agent" in first.supplements[-1].content
    assert not second.full_mode_instruction
    assert "Mode reminder: agent" in second.supplements[-1].content


def test_runtime_uses_full_instruction_after_mode_change() -> None:
    runtime = PromptRuntimeContext()
    runtime.begin_turn("agent")

    plan = runtime.begin_turn("plan-only")
    next_plan = runtime.begin_turn("plan-only")

    assert plan.full_mode_instruction
    assert "implementation plan" in plan.supplements[-1].content
    assert not next_plan.full_mode_instruction
    assert "Mode reminder: plan-only" in next_plan.supplements[-1].content


def test_runtime_keeps_session_supplements_and_not_request_supplements() -> None:
    runtime = PromptRuntimeContext()
    memory = SystemSupplement(
        SupplementKind.MEMORY,
        "The project uses Python 3.12.",
        SupplementScope.SESSION,
    )
    environment = SystemSupplement(
        SupplementKind.ENVIRONMENT,
        "Workspace: D:\\project",
    )
    runtime.set_session_supplement(memory)

    turn = runtime.begin_turn("agent", (environment,))
    later = runtime.begin_turn("agent")

    assert memory in turn.supplements
    assert environment in turn.supplements
    assert memory in later.supplements
    assert environment not in later.supplements
    assert runtime.session_supplements == (memory,)


def test_runtime_replaces_same_session_supplement_kind() -> None:
    runtime = PromptRuntimeContext()
    first = SystemSupplement(
        SupplementKind.TOOL_STATE,
        "tool-a online",
        SupplementScope.SESSION,
    )
    second = SystemSupplement(
        SupplementKind.TOOL_STATE,
        "tool-b online",
        SupplementScope.SESSION,
    )

    runtime.set_session_supplement(first)
    runtime.set_session_supplement(second)

    assert runtime.session_supplements == (second,)


def test_runtime_orders_project_context_before_other_session_supplements() -> None:
    runtime = PromptRuntimeContext()
    memory = SystemSupplement(
        SupplementKind.PROJECT_MEMORY,
        "memory",
        SupplementScope.SESSION,
    )
    tool_state = SystemSupplement(
        SupplementKind.TOOL_STATE,
        "tools",
        SupplementScope.SESSION,
    )
    instructions = SystemSupplement(
        SupplementKind.PROJECT_INSTRUCTIONS,
        "instructions",
        SupplementScope.SESSION,
    )

    for supplement in (tool_state, memory, instructions):
        runtime.set_session_supplement(supplement)

    assert runtime.session_supplements == (instructions, memory, tool_state)


def test_runtime_reset_mode_restores_full_instruction() -> None:
    runtime = PromptRuntimeContext()
    runtime.begin_turn("agent")
    assert not runtime.begin_turn("agent").full_mode_instruction

    runtime.reset_mode()

    assert runtime.begin_turn("agent").full_mode_instruction


def test_turn_context_is_reusable_without_runtime_growth() -> None:
    runtime = PromptRuntimeContext()
    turn = runtime.begin_turn("agent")

    assert turn.supplements is turn.supplements
    assert runtime.session_supplements == ()


def test_runtime_rejects_wrong_supplement_scope() -> None:
    runtime = PromptRuntimeContext()
    request_item = SystemSupplement(SupplementKind.MEMORY, "temporary")
    session_item = SystemSupplement(
        SupplementKind.MEMORY,
        "persistent",
        SupplementScope.SESSION,
    )

    with pytest.raises(ValueError, match="session"):
        runtime.set_session_supplement(request_item)
    with pytest.raises(ValueError, match="request"):
        runtime.begin_turn("agent", (session_item,))
