import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ycode.agent import AgentMode, AgentRequestSnapshot
from ycode.config import SubagentConfig
from ycode.core import AgentModelRequest, ChatMessage, TokenUsage
from ycode.security import PermissionMode
from ycode.subagents import (
    RunSubagentArguments,
    SubagentCreationMode,
    SubagentError,
    SubagentIsolation,
    SubagentManager,
    SubagentManagerError,
    SubagentRoleConfig,
    SubagentRoleSnapshot,
    SubagentRunMode,
    SubagentStatus,
    SubagentTaskView,
)
from ycode.worktrees import WorktreeManagerError

BASE_TIME = datetime(2026, 8, 16, tzinfo=UTC)


class FakeCatalog:
    def __init__(self) -> None:
        self.role = SubagentRoleSnapshot(
            SubagentRoleConfig("review", "review", "work"),
            "review.md",
        )

    def get_available(self, name: str):
        return self.role if name.casefold() == "review" else None


class ControlledRunner:
    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked
        self.gates: dict[str, asyncio.Event] = {}
        self.completion_count = 0

    async def run(self, task_id, invocation, parent):
        del parent
        gate = self.gates.setdefault(task_id, asyncio.Event())
        if self.blocked:
            await gate.wait()
        self.completion_count += 1
        return SubagentTaskView(
            task_id,
            SubagentStatus.COMPLETED,
            invocation.creation_mode,
            invocation.run_mode,
            invocation.role.config.name if invocation.role is not None else None,
            invocation.task,
            f"result-{task_id}",
            TokenUsage(input_tokens=3, output_tokens=2),
            BASE_TIME,
            BASE_TIME + timedelta(seconds=self.completion_count),
        )


def parent(turn_id: str = "turn-1") -> AgentRequestSnapshot:
    return AgentRequestSnapshot(
        turn_id,
        AgentModelRequest(messages=(ChatMessage.user_text("parent"),)),
        AgentMode.AGENT,
        PermissionMode.DEFAULT,
        frozenset({"read_file"}),
    )


def manager(
    runner: ControlledRunner,
    *,
    max_concurrent: int = 4,
    ids: tuple[str, ...] = ("task-1", "task-2", "task-3"),
) -> SubagentManager:
    values = iter(ids)
    result = SubagentManager(
        SubagentConfig(max_concurrent=max_concurrent),
        FakeCatalog(),  # type: ignore[arg-type]
        clock=lambda: BASE_TIME,
        id_factory=lambda: next(values),
    )
    result.bind(runner)  # type: ignore[arg-type]
    return result


async def wait_terminal(current: SubagentManager, task_id: str) -> SubagentTaskView:
    for _ in range(20):
        view = current.get(task_id)
        if view.status.terminal:
            return view
        await asyncio.sleep(0)
    raise AssertionError("任务没有进入终态")


async def test_defined_sync_waits_and_does_not_queue_notification() -> None:
    current = manager(ControlledRunner())

    result = await current.start(
        RunSubagentArguments("inspect", "review"),
        parent(),
    )

    assert result.status is SubagentStatus.COMPLETED
    assert result.creation_mode is SubagentCreationMode.DEFINED
    assert result.run_mode is SubagentRunMode.SYNC
    assert current.take_pending() == ()


async def test_async_returns_running_then_queues_one_notification() -> None:
    runner = ControlledRunner(blocked=True)
    current = manager(runner)

    started = await current.start(RunSubagentArguments("fork task"), parent())

    assert started.status is SubagentStatus.RUNNING
    assert started.creation_mode is SubagentCreationMode.FORK
    assert started.run_mode is SubagentRunMode.ASYNC
    await asyncio.sleep(0)
    runner.gates[started.task_id].set()
    result = await wait_terminal(current, started.task_id)
    notices = current.take_pending()
    assert result.status is SubagentStatus.COMPLETED
    assert len(notices) == 1
    assert started.task_id in notices[0].content
    assert current.take_pending() == ()


async def test_concurrency_limit_fails_immediately_without_queue() -> None:
    runner = ControlledRunner(blocked=True)
    current = manager(runner, max_concurrent=1)
    first = await current.start(RunSubagentArguments("first"), parent())

    with pytest.raises(SubagentManagerError) as caught:
        await current.start(RunSubagentArguments("second"), parent())

    assert caught.value.code == "concurrency_limit"
    assert len(current.tasks) == 1
    await current.stop(first.task_id)


async def test_notifications_follow_completion_order() -> None:
    runner = ControlledRunner(blocked=True)
    current = manager(runner)
    first = await current.start(RunSubagentArguments("first"), parent())
    second = await current.start(RunSubagentArguments("second"), parent())

    await asyncio.sleep(0)
    runner.gates[second.task_id].set()
    await wait_terminal(current, second.task_id)
    runner.gates[first.task_id].set()
    await wait_terminal(current, first.task_id)

    notices = current.take_pending()
    assert second.task_id in notices[0].content
    assert first.task_id in notices[1].content


async def test_stop_owner_cancel_and_clear_use_exact_boundaries() -> None:
    runner = ControlledRunner(blocked=True)
    current = manager(runner)
    owned = await current.start(RunSubagentArguments("owned"), parent("turn-owned"))
    other = await current.start(RunSubagentArguments("other"), parent("turn-other"))

    await current.cancel_owned("turn-owned")

    assert current.get(owned.task_id).status is SubagentStatus.CANCELLED
    assert current.get(other.task_id).status is SubagentStatus.RUNNING
    stopped = await current.stop(other.task_id[:6])
    assert stopped.status is SubagentStatus.CANCELLED
    await current.clear()
    assert current.tasks == ()
    assert current.take_pending() == ()


async def test_validation_and_two_phase_binding_errors_are_explicit() -> None:
    unbound = SubagentManager(
        SubagentConfig(),
        FakeCatalog(),  # type: ignore[arg-type]
    )
    with pytest.raises(SubagentManagerError) as not_ready:
        await unbound.start(RunSubagentArguments("task", "review"), parent())
    assert not_ready.value.code == "manager_not_ready"

    current = manager(ControlledRunner())
    with pytest.raises(SubagentManagerError) as empty:
        await current.start(RunSubagentArguments("  ", "review"), parent())
    assert empty.value.code == "task_empty"
    with pytest.raises(SubagentManagerError) as missing:
        await current.start(RunSubagentArguments("task", "missing"), parent())
    assert missing.value.code == "role_not_found"
    with pytest.raises(SubagentManagerError) as fork_sync:
        await current.start(
            RunSubagentArguments("task", mode=SubagentRunMode.SYNC),
            parent(),
        )
    assert fork_sync.value.code == "fork_sync_invalid"


def test_terminal_stop_and_ambiguous_prefix_are_rejected() -> None:
    current = manager(
        ControlledRunner(),
        ids=("abc-one", "abc-two", "abc-three"),
    )
    first = SubagentTaskView(
        "abc-one",
        SubagentStatus.FAILED,
        SubagentCreationMode.FORK,
        SubagentRunMode.ASYNC,
        None,
        "task",
        None,
        TokenUsage(),
        BASE_TIME,
        BASE_TIME,
        SubagentError("failed", "failed"),
    )
    second = SubagentTaskView(
        "abc-two",
        SubagentStatus.CANCELLED,
        SubagentCreationMode.FORK,
        SubagentRunMode.ASYNC,
        None,
        "task",
        None,
        TokenUsage(),
        BASE_TIME,
        BASE_TIME,
        SubagentError("cancelled", "cancelled"),
    )
    from ycode.subagents.models import ManagedSubagentTask

    current._tasks = {  # type: ignore[attr-defined]
        first.task_id: ManagedSubagentTask(first, "turn"),
        second.task_id: ManagedSubagentTask(second, "turn"),
    }

    with pytest.raises(SubagentManagerError) as ambiguous:
        current.get("abc")
    assert ambiguous.value.code == "task_id_ambiguous"


@pytest.mark.asyncio
async def test_shared_fallback_token_requires_later_matching_turn_and_is_one_time() -> None:
    class IsolatedCatalog:
        role = SubagentRoleSnapshot(
            SubagentRoleConfig(
                "review",
                "review",
                "work",
                isolation=SubagentIsolation.WORKTREE,
            ),
            "review.md",
        )

        def get_available(self, name: str):
            return self.role if name == "review" else None

    class UnavailableWorktree:
        project_root = "C:/project"

        async def acquire(self, role: str, session: str, task: str):
            del role, session, task
            raise WorktreeManagerError("git_unavailable", "Git 不可用")

    runner = ControlledRunner()
    task_numbers = iter(range(1, 10))
    current = SubagentManager(
        SubagentConfig(),
        IsolatedCatalog(),  # type: ignore[arg-type]
        id_factory=lambda: f"task-isolated-{next(task_numbers)}",
        session_id_provider=lambda: "session-a",
        worktree_manager=UnavailableWorktree(),  # type: ignore[arg-type]
        fallback_token_factory=lambda: "grant-token",
        clock=lambda: BASE_TIME,
    )
    current.bind(runner)  # type: ignore[arg-type]

    with pytest.raises(SubagentManagerError) as unavailable:
        await current.start(RunSubagentArguments("inspect", "review"), parent("turn-1"))
    assert unavailable.value.code == "isolation_unavailable"
    assert "grant-token" in str(unavailable.value)
    assert current.tasks == ()

    arguments = RunSubagentArguments("inspect", "review", None, "grant-token")
    with pytest.raises(SubagentManagerError) as same_turn:
        await current.start(arguments, parent("turn-1"))
    assert same_turn.value.code == "fallback_same_turn"
    with pytest.raises(SubagentManagerError) as mismatch:
        await current.start(
            RunSubagentArguments("changed", "review", None, "grant-token"),
            parent("turn-2"),
        )
    assert mismatch.value.code == "fallback_mismatch"

    result = await current.start(arguments, parent("turn-2"))
    assert result.status is SubagentStatus.COMPLETED
    with pytest.raises(SubagentManagerError) as reused:
        await current.start(arguments, parent("turn-3"))
    assert reused.value.code == "fallback_token_invalid"


@pytest.mark.asyncio
async def test_tool_isolation_overrides_role_default_and_supports_fork() -> None:
    class IsolatedCatalog:
        role = SubagentRoleSnapshot(
            SubagentRoleConfig(
                "review",
                "review",
                "work",
                isolation=SubagentIsolation.WORKTREE,
            ),
            "review.md",
        )

        def get_available(self, name: str):
            return self.role if name == "review" else None

    class UnavailableWorktree:
        project_root = "C:/project"
        requested_roles: list[str] = []

        async def acquire(self, role: str, session: str, task: str):
            del session, task
            self.requested_roles.append(role)
            raise WorktreeManagerError("git_unavailable", "Git 不可用")

    worktrees = UnavailableWorktree()
    current = SubagentManager(
        SubagentConfig(),
        IsolatedCatalog(),  # type: ignore[arg-type]
        id_factory=iter(("task-local", "task-role", "task-fork")).__next__,
        session_id_provider=lambda: "session-a",
        worktree_manager=worktrees,  # type: ignore[arg-type]
        clock=lambda: BASE_TIME,
    )
    current.bind(ControlledRunner())  # type: ignore[arg-type]

    local = await current.start(
        RunSubagentArguments(
            "local override",
            "review",
            isolation=SubagentIsolation.NONE,
        ),
        parent("turn-local"),
    )
    assert local.status is SubagentStatus.COMPLETED
    assert worktrees.requested_roles == []

    with pytest.raises(SubagentManagerError) as role_default:
        await current.start(RunSubagentArguments("role default", "review"), parent("turn-role"))
    assert role_default.value.code == "isolation_unavailable"
    assert worktrees.requested_roles == ["review"]

    with pytest.raises(SubagentManagerError) as fork_override:
        await current.start(
            RunSubagentArguments("fork isolated", isolation=SubagentIsolation.WORKTREE),
            parent("turn-fork"),
        )
    assert fork_override.value.code == "isolation_unavailable"
    assert worktrees.requested_roles == ["review", "fork"]
