from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from ycode.core.messages import ToolCallBlock
from ycode.security import (
    CommandSafetyResult,
    PermissionAction,
    PermissionEngine,
    PermissionMode,
    PermissionSession,
    SecurityConfig,
    SecurityRule,
)
from ycode.tools import (
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    create_builtin_registry,
)
from ycode.tools.command import CommandResult
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService

ALL_ACCESS = frozenset(ToolAccess)


class FakeCommandRunner:
    async def run(self, command: str, cwd: Path) -> CommandResult:
        return CommandResult(0, "", "", 0, False)


class FakeChecker:
    def __init__(self, result: CommandSafetyResult | None = None) -> None:
        self.result = result or CommandSafetyResult(safe=True)

    async def check(self, command: str) -> CommandSafetyResult:
        return self.result


class UnknownArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    count: int = 1


class UnknownTool:
    definition = ToolDefinition(
        name="unknown_adapter",
        description="安全分类未知的工具",
        access=ToolAccess.UNKNOWN,
        arguments_model=UnknownArguments,
    )
    timeout_seconds = 1.0

    async def execute(
        self,
        arguments: UnknownArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content=arguments.value)


def make_engine(
    workspace: Path,
    *,
    config: SecurityConfig | None = None,
    checker: FakeChecker | None = None,
) -> PermissionEngine:
    resolver = WorkspacePathResolver(workspace)
    registry = create_builtin_registry(
        resolver,
        TextFileService(),
        FakeCommandRunner(),
    )
    registry.register(UnknownTool())
    return PermissionEngine(
        registry,
        resolver,
        config or SecurityConfig(),
        checker or FakeChecker(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_normalizes_real_path_and_uses_exact_tool_session_key(
    tmp_path: Path,
) -> None:
    target = tmp_path / "real.txt"
    target.write_text("hello", encoding="utf-8")
    engine = make_engine(tmp_path)
    session = PermissionSession(PermissionMode.STRICT)
    first = await engine.evaluate(
        ToolCallBlock(
            id="one",
            name="read_file",
            arguments={"path": "real.txt", "offset": 10, "limit": 1},
        ),
        session,
        allowed_access=ALL_ACCESS,
    )
    session.grant(first.subject.session_key)
    second = await engine.evaluate(
        ToolCallBlock(
            id="two",
            name="read_file",
            arguments={"path": ".\\real.txt", "offset": 1, "limit": 2000},
        ),
        session,
        allowed_access=ALL_ACCESS,
    )

    assert first.subject.normalized_arguments["path"] == "real.txt"
    assert first.subject.session_key == {"tool": "read_file", "path": "real.txt"}
    assert second.action is PermissionAction.ALLOW
    assert second.reason_code == "session_grant"


@pytest.mark.asyncio
async def test_write_preview_is_bounded_but_run_command_remains_complete(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    session = PermissionSession()
    write = await engine.evaluate(
        ToolCallBlock(
            id="write",
            name="write_file",
            arguments={"path": "a.txt", "content": "中" * 3000},
        ),
        session,
        allowed_access=ALL_ACCESS,
    )
    command_text = "Write-Output '" + ("x" * 3000) + "'"
    command = await engine.evaluate(
        ToolCallBlock(
            id="command",
            name="run_command",
            arguments={"command": command_text},
        ),
        session,
        allowed_access=ALL_ACCESS,
    )

    assert len(write.subject.approval_summary.encode("utf-8")) <= 2200
    assert "内容已截断" in write.subject.approval_summary
    assert command_text in command.subject.approval_summary


@pytest.mark.asyncio
async def test_first_matching_project_rule_wins(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("", encoding="utf-8")
    config = SecurityConfig(
        mode=PermissionMode.ALLOW,
        rules=(
            SecurityRule(
                id="ask-markdown",
                action="ask",
                tool="read_file",
                arguments={"path": {"glob": "*.md"}},
            ),
            SecurityRule(id="allow-all-read", action="allow", tool="read_file"),
        ),
    )
    engine = make_engine(tmp_path, config=config)

    decision = await engine.evaluate(
        ToolCallBlock(
            id="read",
            name="read_file",
            arguments={"path": "NOTES.md"},
        ),
        PermissionSession(PermissionMode.ALLOW),
        allowed_access=ALL_ACCESS,
    )

    assert decision.action is PermissionAction.ASK
    assert decision.rule_id == "ask-markdown"


@pytest.mark.asyncio
async def test_modes_and_unknown_defaults(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    engine = make_engine(tmp_path)

    strict_read = await engine.evaluate(
        ToolCallBlock(id="read", name="read_file", arguments={"path": "a.txt"}),
        PermissionSession(PermissionMode.STRICT),
        allowed_access=ALL_ACCESS,
    )
    default_read = await engine.evaluate(
        ToolCallBlock(id="read", name="read_file", arguments={"path": "a.txt"}),
        PermissionSession(PermissionMode.DEFAULT),
        allowed_access=ALL_ACCESS,
    )
    allow_run = await engine.evaluate(
        ToolCallBlock(
            id="run",
            name="run_command",
            arguments={"command": "Get-ChildItem"},
        ),
        PermissionSession(PermissionMode.ALLOW),
        allowed_access=ALL_ACCESS,
    )
    unknown = await engine.evaluate(
        ToolCallBlock(
            id="unknown",
            name="unknown_adapter",
            arguments={"value": "x", "count": 2},
        ),
        PermissionSession(PermissionMode.ALLOW),
        allowed_access=ALL_ACCESS,
    )

    assert strict_read.action is PermissionAction.ASK
    assert default_read.action is PermissionAction.ALLOW
    assert allow_run.action is PermissionAction.ALLOW
    assert unknown.action is PermissionAction.ASK
    assert unknown.subject.session_key["arguments"]["count"] == 2  # type: ignore[index]


@pytest.mark.asyncio
async def test_hard_rules_override_session_rule_and_allow_mode(tmp_path: Path) -> None:
    config = SecurityConfig(
        mode=PermissionMode.ALLOW,
        rules=(SecurityRule(id="allow-command", action="allow", tool="run_command"),),
    )
    engine = make_engine(
        tmp_path,
        config=config,
        checker=FakeChecker(CommandSafetyResult(False, "disk_damage", "禁止磁盘破坏。")),
    )
    session = PermissionSession(PermissionMode.ALLOW)
    session.grant({"tool": "run_command", "command": "Format-Volume C", "cwd": "."})

    decision = await engine.evaluate(
        ToolCallBlock(
            id="run",
            name="run_command",
            arguments={"command": "Format-Volume C"},
        ),
        session,
        allowed_access=ALL_ACCESS,
    )

    assert decision.action is PermissionAction.DENY
    assert decision.reason_code == "hard_disk_damage"


@pytest.mark.asyncio
async def test_plan_only_and_outside_path_are_hard_denials(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    plan_only = await engine.evaluate(
        ToolCallBlock(
            id="write",
            name="write_file",
            arguments={"path": "new.txt", "content": "x"},
        ),
        PermissionSession(PermissionMode.ALLOW),
        allowed_access=frozenset({ToolAccess.READ}),
    )
    outside = await engine.evaluate(
        ToolCallBlock(
            id="outside",
            name="write_file",
            arguments={"path": str(tmp_path.parent / "outside.txt"), "content": "x"},
        ),
        PermissionSession(PermissionMode.ALLOW),
        allowed_access=ALL_ACCESS,
    )

    assert plan_only.action is PermissionAction.DENY
    assert plan_only.reason_code == "access_not_available"
    assert outside.action is PermissionAction.DENY
    assert outside.reason_code == "invalid_or_unsafe_arguments"


@pytest.mark.asyncio
async def test_symlink_uses_real_target_instead_of_rejecting_link_itself(
    tmp_path: Path,
) -> None:
    inside = tmp_path / "inside.txt"
    inside.write_text("safe", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("unsafe", encoding="utf-8")
    inside_link = tmp_path / "inside-link.txt"
    outside_link = tmp_path / "outside-link.txt"
    try:
        inside_link.symlink_to(inside)
        outside_link.symlink_to(outside)
    except OSError:
        pytest.skip("当前 Windows 环境不允许创建符号链接")
    engine = make_engine(tmp_path)

    allowed = await engine.evaluate(
        ToolCallBlock(
            id="inside",
            name="read_file",
            arguments={"path": "inside-link.txt"},
        ),
        PermissionSession(PermissionMode.DEFAULT),
        allowed_access=ALL_ACCESS,
    )
    denied = await engine.evaluate(
        ToolCallBlock(
            id="outside",
            name="read_file",
            arguments={"path": "outside-link.txt"},
        ),
        PermissionSession(PermissionMode.ALLOW),
        allowed_access=ALL_ACCESS,
    )

    assert allowed.action is PermissionAction.ALLOW
    assert allowed.subject.normalized_arguments["path"] == "inside.txt"
    assert denied.action is PermissionAction.DENY
