"""内置命令框架。"""

from ycode.commands.builtin import build_command_runtime
from ycode.commands.contracts import (
    CommandCompletionEntry,
    CommandDefinition,
    CommandInvocation,
    CommandKind,
    CommandRuntime,
    UIController,
)
from ycode.commands.dispatcher import CommandDispatcher
from ycode.commands.errors import (
    CommandConflictError,
    CommandDefinitionError,
    CommandExecutionError,
    CommandUsageError,
)
from ycode.commands.parser import CommandParser
from ycode.commands.registry import CommandRegistry

__all__ = [
    "CommandCompletionEntry",
    "CommandConflictError",
    "CommandDefinition",
    "CommandDefinitionError",
    "CommandDispatcher",
    "CommandExecutionError",
    "CommandInvocation",
    "CommandKind",
    "CommandParser",
    "CommandRegistry",
    "CommandRuntime",
    "CommandUsageError",
    "UIController",
    "build_command_runtime",
]
