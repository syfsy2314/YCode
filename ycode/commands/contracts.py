"""命令定义、调用与 UI 控制契约。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ycode.commands.dispatcher import CommandDispatcher
    from ycode.commands.registry import CommandRegistry


class CommandKind(StrEnum):
    LOCAL = "local"
    STATE = "state"
    AI = "ai"


@dataclass(frozen=True, slots=True)
class CommandInvocation:
    raw_text: str
    name: str
    arguments: str


class UIController(Protocol):
    async def show_user_input(self, text: str) -> None: ...

    async def show_system_message(self, message: str) -> None: ...

    async def send_user_message(self, display_text: str, model_text: str) -> None: ...

    async def set_mode(self, mode: str) -> None: ...

    async def show_mcp_status(self) -> None: ...

    async def compact_context(self) -> None: ...

    async def show_permission_status(self) -> None: ...

    async def set_permission_mode(self, mode: str) -> None: ...

    async def clear_permission_grants(self) -> None: ...

    async def resume_session(self, session_id: str) -> None: ...

    async def refresh_status(self) -> None: ...

    async def request_exit(self) -> None: ...


CommandHandler = Callable[[CommandInvocation, UIController], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str
    kind: CommandKind
    argument_hint: str
    handler: CommandHandler
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class CommandCompletionEntry:
    text: str
    description: str
    command_name: str


@dataclass(frozen=True, slots=True)
class CommandRuntime:
    registry: CommandRegistry
    dispatcher: CommandDispatcher
