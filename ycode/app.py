"""YCode 应用装配。"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from ycode.agent import AgentLoop, PlainChatRunner
from ycode.config.discovery import discover_config
from ycode.config.loader import load_config
from ycode.config.models import ProviderConfig, ProviderProtocol
from ycode.core.provider import AgentChatProvider, ChatProvider
from ycode.prompt import EnvironmentCollector, PromptRuntimeContext, build_builtin_prompt
from ycode.providers.factory import create_provider
from ycode.security import (
    PermissionEngine,
    PermissionSession,
    PowerShellSafetyChecker,
    load_security_config,
)
from ycode.session.chat import ChatSession
from ycode.tools import ToolContext, ToolExecutor, ToolScheduler, create_builtin_registry
from ycode.tools.command import PowerShellCommandRunner
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService
from ycode.ui.terminal import TerminalUI

ProviderFactory = Callable[[ProviderConfig], ChatProvider]
UIFactory = Callable[[ProviderConfig, ChatSession], Any]


def _resolve_workspace(start_dir: str | Path | None) -> Path:
    return Path(start_dir or Path.cwd()).resolve()


async def run_app(
    config_path: str | Path | None = None,
    *,
    start_dir: str | Path | None = None,
    provider_factory: ProviderFactory = create_provider,
    ui_factory: UIFactory = TerminalUI,
) -> None:
    path = discover_config(config_path, start_dir=start_dir)
    config = load_config(path)
    provider = provider_factory(config.active_provider)
    workspace = _resolve_workspace(start_dir)
    permission_session: PermissionSession | None = None
    if config.active_provider.protocol is ProviderProtocol.ANTHROPIC:
        resolver = WorkspacePathResolver(workspace)
        registry = create_builtin_registry(
            resolver,
            TextFileService(),
            PowerShellCommandRunner(),
        )
        executor = ToolExecutor(registry)
        security_config = load_security_config(workspace, registry)
        permission_session = PermissionSession(security_config.mode)
        permission_engine = PermissionEngine(
            registry,
            resolver,
            security_config,
            PowerShellSafetyChecker(workspace),
        )
        runner = AgentLoop(
            cast(AgentChatProvider, provider),
            registry,
            ToolScheduler(registry, executor),
            build_builtin_prompt(),
            PromptRuntimeContext(),
            EnvironmentCollector(workspace),
            ToolContext(workspace),
            permission_engine=permission_engine,
            permission_session=permission_session,
        )
    else:
        runner = PlainChatRunner(provider)
    session = ChatSession(runner, permission_session)
    try:
        ui = ui_factory(config.active_provider, session)
        await ui.run()
    finally:
        await session.close()
