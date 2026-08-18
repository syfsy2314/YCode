"""隔离子 Agent 的独立工作区运行时。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ycode.agent import AgentLoop, AgentLoopOptions
from ycode.config import SecretRedactor
from ycode.context import (
    ContextArtifactStore,
    ContextManager,
    ContextPolicy,
    ConversationCompactor,
)
from ycode.hooks import HookContextFactory, HookRule, HookRuntime
from ycode.memory import MemoryStore
from ycode.prompt import (
    EnvironmentCollector,
    ProjectContextLoader,
    PromptBundle,
    PromptRuntimeContext,
    PromptSection,
    build_builtin_prompt,
)
from ycode.security import (
    PermissionEngine,
    PermissionSession,
    PowerShellSafetyChecker,
    SecurityConfig,
)
from ycode.subagents.policy import SubagentToolPolicy
from ycode.subagents.runner import SubagentRuntimeRequest
from ycode.tools import ToolContext, ToolExecutor, ToolScheduler, create_builtin_registry
from ycode.tools.builtin.tool_search import ToolSearchTool
from ycode.tools.command import PowerShellCommandRunner
from ycode.tools.paths import WorkspaceMount, WorkspacePathResolver
from ycode.tools.registry import ToolRegistry
from ycode.tools.text_files import TextFileService
from ycode.worktrees.models import WorktreeLease

_WORKSPACE_TOOLS = frozenset(
    {"read_file", "write_file", "edit_file", "run_command", "glob", "grep", "tool_search"}
)


class _OwnedResources:
    def __init__(self, context: ContextManager, hooks: HookRuntime) -> None:
        self._context = context
        self._hooks = hooks

    async def close(self) -> None:
        try:
            await self._context.close()
        finally:
            await self._hooks.close()


@dataclass(frozen=True, slots=True)
class SubagentWorkspaceRuntime:
    loop: AgentLoop
    registry: ToolRegistry
    resolver: WorkspacePathResolver
    prompt_runtime: PromptRuntimeContext
    context_manager: ContextManager
    hook_runtime: HookRuntime


class SubagentWorkspaceFactory:
    def __init__(
        self,
        project_root: str | Path,
        main_registry: ToolRegistry,
        security: SecurityConfig,
        redactor: SecretRedactor,
        context_policy: ContextPolicy,
        hook_rules: tuple[HookRule, ...],
    ) -> None:
        self._project_root = Path(project_root).resolve(strict=True)
        self._main_registry = main_registry
        self._security = security
        self._redactor = redactor
        self._context_policy = context_policy
        self._hook_rules = hook_rules

    def create(
        self,
        request: SubagentRuntimeRequest,
        lease: WorktreeLease,
    ) -> SubagentWorkspaceRuntime:
        workspace = lease.path.resolve(strict=True)
        resolver = WorkspacePathResolver(workspace, mounts=self._mounts(lease))
        registry = create_builtin_registry(
            resolver,
            TextFileService(),
            PowerShellCommandRunner(lease.environment),
        )
        for tool in self._main_registry:
            if tool.definition.name not in _WORKSPACE_TOOLS:
                registry.register(tool)
        if self._main_registry.get("tool_search") is not None:
            registry.register(ToolSearchTool(registry))

        project_context = ProjectContextLoader(
            workspace,
            MemoryStore(self._project_root),
        ).load()
        prompt_runtime = PromptRuntimeContext()
        for supplement in project_context.supplements:
            prompt_runtime.set_session_supplement(supplement)
        prompt_bundle = build_builtin_prompt()
        if request.role is not None:
            prompt_bundle = PromptBundle(
                (
                    *prompt_bundle.sections,
                    PromptSection("subagent-role", 90, request.role.config.prompt),
                )
            )
        context = ContextManager(
            self._context_policy,
            ContextArtifactStore(workspace, self._redactor, self._context_policy),
            ConversationCompactor(request.provider),
        )
        hooks = HookRuntime(self._hook_rules, workspace)
        hook_context = HookContextFactory(
            workspace,
            request.hook_scope_id,
            task_metadata={
                "task_id": request.task_id,
                "creation_mode": "defined",
                "role": request.role.config.name if request.role is not None else None,
                "run_mode": request.run_mode.value,
            },
        )
        permission = PermissionEngine(
            registry,
            resolver,
            self._security,
            PowerShellSafetyChecker(workspace),
        )
        policy = SubagentToolPolicy(
            registry,
            request.policy.base_tool_names,
            role=request.role,
            run_mode=request.run_mode,
            async_allowed_tools=request.policy.async_allowed_tools,
            plan_only=request.mode.value == "plan_only",
        )
        loop = AgentLoop(
            request.provider,
            registry,
            ToolScheduler(registry, ToolExecutor(registry)),
            prompt_bundle,
            prompt_runtime,
            EnvironmentCollector(workspace),
            ToolContext(workspace),
            permission_engine=permission,
            permission_session=PermissionSession(request.permission_mode),
            context_manager=context,
            resource_manager=_OwnedResources(context, hooks),
            hook_runtime=hooks,
            hook_context=hook_context,
            max_rounds=request.max_rounds,
            options=AgentLoopOptions(
                tool_policy=policy,
                hook_scope_id=request.hook_scope_id,
                non_interactive_approvals=True,
                owns_provider=False,
                clear_hook_scope_on_finish=True,
                preserve_seed_prefix=request.preserve_seed_prefix,
            ),
        )
        return SubagentWorkspaceRuntime(
            loop,
            registry,
            resolver,
            prompt_runtime,
            context,
            hooks,
        )

    def _mounts(self, lease: WorktreeLease) -> tuple[WorkspaceMount, ...]:
        mounts: list[WorkspaceMount] = []
        memory = self._project_root / ".ycode" / "memory"
        if memory.is_dir():
            mounts.append(WorkspaceMount(Path(".ycode/memory"), memory, virtual=True))
        for relative in lease.record.linked_directories:
            parts = PurePosixPath(relative).parts
            mounts.append(
                WorkspaceMount(
                    Path(*parts),
                    self._project_root.joinpath(*parts),
                    writable=True,
                    command_cwd_allowed=True,
                )
            )
        return tuple(mounts)
