from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ycode.core import TokenUsage
from ycode.security import PermissionMode
from ycode.subagents import (
    SubagentConfig,
    SubagentCreationMode,
    SubagentIsolation,
    SubagentRoleConfig,
    SubagentRunMode,
    SubagentStatus,
    SubagentTaskView,
)


def test_subagent_config_defaults_and_validates() -> None:
    config = SubagentConfig()

    assert config.max_concurrent == 4
    assert "write_file" in config.async_allowed_tools

    for value in (0, True, "4"):
        with pytest.raises(ValidationError):
            SubagentConfig(max_concurrent=value)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="重复"):
        SubagentConfig(async_allowed_tools=("grep", "grep"))


def test_role_config_freezes_tool_sets_and_validates_rounds() -> None:
    role = SubagentRoleConfig(
        "review",
        "Review code",
        "Inspect the code",
        allowed_tools={"read_file"},  # type: ignore[arg-type]
        denied_tools={"run_command"},  # type: ignore[arg-type]
        permission=PermissionMode.STRICT,
    )

    assert role.allowed_tools == frozenset({"read_file"})
    assert role.denied_tools == frozenset({"run_command"})
    assert role.isolation is SubagentIsolation.NONE
    isolated = SubagentRoleConfig(
        "writer",
        "Write code",
        "Implement changes",
        isolation=SubagentIsolation.WORKTREE,
    )
    assert isolated.isolation is SubagentIsolation.WORKTREE
    with pytest.raises(ValueError, match="正整数"):
        SubagentRoleConfig("bad", "bad", "bad", max_rounds=0)


def test_task_status_and_view_are_provider_neutral() -> None:
    view = SubagentTaskView(
        "task-1",
        SubagentStatus.RUNNING,
        SubagentCreationMode.FORK,
        SubagentRunMode.ASYNC,
        None,
        "inspect",
        None,
        TokenUsage(input_tokens=2),
        datetime.now(UTC),
    )

    assert not view.status.terminal
    assert SubagentStatus.COMPLETED.terminal
