from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from ycode.errors import ConfigError
from ycode.security import (
    PermissionAction,
    PermissionMode,
    discover_security_config,
    load_security_config,
)
from ycode.tools import (
    PydanticToolArguments,
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRegistry,
)


class ExampleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


class ExampleTool:
    definition = ToolDefinition(
        name="example_tool",
        description="测试工具",
        access=ToolAccess.READ,
        arguments=PydanticToolArguments(ExampleArguments),
    )
    timeout_seconds = 1.0

    async def execute(
        self,
        arguments: ExampleArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content=arguments.path)


@pytest.fixture
def registry() -> ToolRegistry:
    result = ToolRegistry()
    result.register(ExampleTool())
    return result


def test_missing_config_uses_default_mode_and_empty_rules(
    tmp_path: Path,
    registry: ToolRegistry,
) -> None:
    result = load_security_config(tmp_path, registry)
    config = result.config

    assert config.mode is PermissionMode.DEFAULT
    assert config.rules == ()


def test_discovers_nearest_config_while_walking_up(tmp_path: Path) -> None:
    parent = tmp_path / "project"
    child = parent / "src" / "nested"
    child.mkdir(parents=True)
    parent_config = parent / ".ycode" / "security.yaml"
    child_config = parent / "src" / ".ycode" / "security.yaml"
    parent_config.parent.mkdir()
    child_config.parent.mkdir()
    parent_config.write_text("mode: strict\n", encoding="utf-8")
    child_config.write_text("mode: allow\n", encoding="utf-8")

    assert discover_security_config(child) == child_config


def test_loads_rules_and_validates_tool_arguments(
    tmp_path: Path,
    registry: ToolRegistry,
) -> None:
    config_path = tmp_path / ".ycode" / "security.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        """
mode: strict
rules:
  - id: allow-readme
    action: allow
    tool: example_tool
    arguments:
      path:
        glob: "*.md"
""".strip(),
        encoding="utf-8",
    )

    result = load_security_config(tmp_path, registry)
    config = result.config

    assert config.mode is PermissionMode.STRICT
    assert config.rules[0].action is PermissionAction.ALLOW
    assert config.rules[0].arguments["path"].glob == "*.md"
    assert result.warnings == ()


def test_unavailable_mcp_references_warn_and_continue(
    tmp_path: Path,
    registry: ToolRegistry,
) -> None:
    config_path = tmp_path / ".ycode" / "security.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        """
rules:
  - id: allow-later
    action: allow
    tool: mcp_later_echo
    arguments:
      not_yet_known:
        exact: x
plan_only:
  allow_mcp_tools:
    - mcp_later_echo
""".strip(),
        encoding="utf-8",
    )

    result = load_security_config(tmp_path, registry)

    assert result.config.rules[0].tool == "mcp_later_echo"
    assert [warning.code for warning in result.warnings] == [
        "mcp_rule_tool_unavailable",
        "plan_only_mcp_tool_unavailable",
    ]
    assert all(warning.tool_name == "mcp_later_echo" for warning in result.warnings)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("mode: default\nextra: true\n", "额外"),
        (
            "rules:\n  - id: unknown-tool\n    action: allow\n    tool: missing\n",
            "未知工具",
        ),
        (
            "rules:\n  - id: unknown-argument\n    action: ask\n"
            "    tool: example_tool\n    arguments:\n      missing:\n"
            '        exact: "x"\n',
            "未知参数",
        ),
    ],
)
def test_invalid_config_becomes_safe_config_error(
    tmp_path: Path,
    registry: ToolRegistry,
    body: str,
    message: str,
) -> None:
    config_path = tmp_path / ".ycode" / "security.yaml"
    config_path.parent.mkdir()
    config_path.write_text(body, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_security_config(tmp_path, registry)
