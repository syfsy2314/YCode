"""YCode 应用装配。"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from ycode.agent import AgentLoop, PlainChatRunner
from ycode.commands import CommandDefinitionError, build_command_runtime
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
from ycode.errors import ConfigError
from ycode.mcp.manager import McpManager
from ycode.memory import MemoryStore, MemoryUpdater
from ycode.prompt import (
    EnvironmentCollector,
    ProjectContextLoader,
    PromptRuntimeContext,
    build_builtin_prompt,
)
from ycode.providers.factory import create_provider
from ycode.security import (
    PermissionEngine,
    PermissionSession,
    PowerShellSafetyChecker,
    load_security_config,
)
from ycode.session.chat import ChatSession
from ycode.session.manager import SessionManager
from ycode.tools import ToolContext, ToolExecutor, ToolScheduler, create_builtin_registry
from ycode.tools.builtin.tool_search import ToolSearchTool
from ycode.tools.command import PowerShellCommandRunner
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService
from ycode.ui.terminal import TerminalUI

ProviderFactory = Callable[[ProviderConfig], ChatProvider]
UIFactory = Callable[[ProviderConfig, ChatSession], Any]


async def run_app(
    config_path: str | Path | None = None,
    *,
    start_dir: str | Path | None = None,
    provider_factory: ProviderFactory = create_provider,
    ui_factory: UIFactory = TerminalUI,
    continue_session: bool = False,
) -> None:
    path = discover_config(config_path, start_dir=start_dir)
    config = load_config(path)
    if continue_session and config.active_provider.protocol is ProviderProtocol.OPENAI:
        raise ConfigError("--continue 当前仅支持 Anthropic 会话")
    provider = provider_factory(config.active_provider)
    workspace = config.project_root
    permission_session: PermissionSession | None = None
    manager: McpManager | None = None
    context_manager: ContextManager | None = None
    session: ChatSession | None = None
    command_runtime = None
    has_enabled_mcp = False
    try:
        if config.active_provider.protocol is ProviderProtocol.ANTHROPIC:
            try:
                command_runtime = build_command_runtime()
            except CommandDefinitionError as error:
                raise ConfigError("内置命令配置无效") from error
            memory_store = MemoryStore(workspace)
            project_context = ProjectContextLoader(workspace, memory_store).load()
            prompt_runtime = PromptRuntimeContext()
            for supplement in project_context.supplements:
                prompt_runtime.set_session_supplement(supplement)
            session_manager = SessionManager(workspace)
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
                has_enabled_mcp = any(server.enabled for server in config.mcp.servers)
                if has_enabled_mcp:
                    registry.register(ToolSearchTool(registry))
            security_result = load_security_config(workspace, registry)
            if manager is not None:
                manager.set_security_warnings(security_result.warnings)
                if has_enabled_mcp:

                    def refresh_security_warnings() -> None:
                        refreshed = load_security_config(workspace, registry)
                        manager.set_security_warnings(refreshed.warnings)

                    manager.add_startup_callback(refresh_security_warnings)
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
                prompt_runtime,
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
        if config.active_provider.protocol is ProviderProtocol.ANTHROPIC:
            session = ChatSession(
                runner,
                permission_session,
                manager,
                context_manager,
                session_manager=session_manager,
                prompt_runtime=prompt_runtime,
                memory_store=memory_store,
                memory_updater=MemoryUpdater(cast(AgentChatProvider, provider)),
                startup_warnings=tuple(warning.message for warning in project_context.warnings),
                command_runtime=command_runtime,
            )
            if continue_session:
                try:
                    await session.restore()
                except Exception as error:
                    raise ConfigError("最近会话恢复失败") from error
        else:
            session = ChatSession(runner, permission_session, manager, context_manager)
        if manager is not None and has_enabled_mcp:
            manager.start_background()
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
