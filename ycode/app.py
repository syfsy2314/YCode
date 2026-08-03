"""YCode 应用装配。"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from ycode.agent import AgentLoop, PlainChatRunner
from ycode.config.discovery import discover_config
from ycode.config.loader import load_config
from ycode.config.models import ProviderConfig, ProviderProtocol
from ycode.context import (
    ContextArtifactStore,
    ContextManager,
    ContextPolicy,
    ConversationCompactor,
)
from ycode.core.provider import AgentChatProvider, ChatProvider
from ycode.mcp.manager import McpManager
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
from ycode.tools.builtin.tool_search import ToolSearchTool
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
    manager: McpManager | None = None
    context_manager: ContextManager | None = None
    session: ChatSession | None = None
    try:
        if config.active_provider.protocol is ProviderProtocol.ANTHROPIC:
            policy = ContextPolicy(config.app.context_window_tokens)
            context_manager = ContextManager(
                policy,
                ContextArtifactStore(workspace, config.redactor, policy),
                ConversationCompactor(cast(AgentChatProvider, provider)),
            )
            resolver = WorkspacePathResolver(workspace)
            registry = create_builtin_registry(
                resolver,
                TextFileService(),
                PowerShellCommandRunner(),
            )
            if config.mcp.servers or config.mcp.issues:
                manager = McpManager(config.mcp, registry, config.redactor)
                await manager.start()
                if any(server.enabled for server in config.mcp.servers):
                    registry.register(ToolSearchTool(registry))
            security_result = load_security_config(workspace, registry)
            if manager is not None:
                manager.set_security_warnings(security_result.warnings)
            permission_session = PermissionSession(security_result.config.mode)
            permission_engine = PermissionEngine(
                registry,
                resolver,
                security_result.config,
                PowerShellSafetyChecker(workspace),
            )
            runner = AgentLoop(
                cast(AgentChatProvider, provider),
                registry,
                ToolScheduler(registry, ToolExecutor(registry)),
                build_builtin_prompt(),
                PromptRuntimeContext(),
                EnvironmentCollector(workspace),
                ToolContext(workspace),
                permission_engine=permission_engine,
                permission_session=permission_session,
                plan_only_mcp_tools=frozenset(security_result.config.plan_only.allow_mcp_tools),
                resource_manager=manager,
                context_manager=context_manager,
            )
        else:
            runner = PlainChatRunner(provider)
        session = ChatSession(runner, permission_session, manager, context_manager)
        ui = ui_factory(config.active_provider, session)
        await ui.run()
    finally:
        if session is not None:
            await session.close()
        else:
            try:
                if manager is not None:
                    await manager.close()
            finally:
                try:
                    await provider.close()
                finally:
                    if context_manager is not None:
                        await context_manager.close()
