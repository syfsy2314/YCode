from dataclasses import FrozenInstanceError

import pytest

from ycode.commands import CommandExecutionError, CommandInvocation, CommandKind


def test_command_contracts_are_immutable() -> None:
    invocation = CommandInvocation("/HELP", "help", "")
    with pytest.raises(FrozenInstanceError):
        invocation.name = "other"  # type: ignore[misc]


def test_command_kind_values() -> None:
    assert [kind.value for kind in CommandKind] == ["local", "state", "ai"]


def test_execution_error_requires_safe_summary() -> None:
    with pytest.raises(ValueError):
        CommandExecutionError("  ")
