from pathlib import Path

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.agent import AgentMode
from ycode.config import SecretRedactor
from ycode.context import ContextPolicy
from ycode.security import PermissionMode, SecurityConfig
from ycode.subagents import (
    SubagentRoleConfig,
    SubagentRoleSnapshot,
    SubagentRunMode,
    SubagentToolPolicy,
)
from ycode.subagents.runner import SubagentRuntimeRequest
from ycode.tools import create_builtin_registry
from ycode.tools.command import PowerShellCommandRunner
from ycode.tools.errors import ToolError
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService
from ycode.worktrees.runtime import SubagentWorkspaceFactory

from .manager_helpers import initialize_repo, manager


@pytest.mark.asyncio
async def test_workspace_factory_builds_independent_path_runtime_and_memory_mount(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    memory = project / ".ycode" / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("memory index\n", encoding="utf-8")
    worktrees = manager(project)
    lease = await worktrees.acquire("writer", "session", "runtime")
    main_resolver = WorkspacePathResolver(project)
    main_registry = create_builtin_registry(
        main_resolver,
        TextFileService(),
        PowerShellCommandRunner(),
    )
    role = SubagentRoleSnapshot(SubagentRoleConfig("writer", "write", "role"), "role.md")
    policy = SubagentToolPolicy(
        main_registry,
        frozenset(tool.definition.name for tool in main_registry),
        role=role,
        run_mode=SubagentRunMode.SYNC,
        async_allowed_tools=(),
    )
    request = SubagentRuntimeRequest(
        "task",
        FakeProvider([]),
        role,
        SubagentRunMode.SYNC,
        PermissionMode.DEFAULT,
        AgentMode.AGENT,
        policy,
        3,
        "subagent:task",
        False,
    )
    factory = SubagentWorkspaceFactory(
        project,
        main_registry,
        SecurityConfig(),
        SecretRedactor(),
        ContextPolicy(),
        (),
    )

    runtime = factory.create(request, lease)

    assert runtime.registry is not main_registry
    assert runtime.resolver.workspace == lease.path
    assert runtime.resolver.resolve_existing_file("base.txt") == lease.path / "base.txt"
    assert runtime.resolver.resolve_existing_file(".ycode/memory/MEMORY.md") == (
        memory / "MEMORY.md"
    )
    with pytest.raises(ToolError, match="只读"):
        runtime.resolver.resolve_write_target(".ycode/memory/MEMORY.md")
    await runtime.loop.close()
    await worktrees.finalize(lease)
