"""MCP 远端工具名称到 YCode 名称的稳定映射。"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ASCII_ALPHANUMERIC = re.compile(r"[^A-Za-z0-9]+")
_REPEATED_UNDERSCORES = re.compile(r"_+")


@dataclass(frozen=True, slots=True)
class McpToolName:
    public_name: str
    remote_name: str


@dataclass(frozen=True, slots=True)
class McpToolNamingIssue:
    remote_name: str
    message: str


def normalize_tool_name(remote_name: str) -> str:
    """将远端名称转换为小写 snake_case，不保留非 ASCII 字符。"""

    with_boundaries = _CAMEL_BOUNDARY.sub("_", remote_name)
    normalized = _NON_ASCII_ALPHANUMERIC.sub("_", with_boundaries).lower()
    return _REPEATED_UNDERSCORES.sub("_", normalized).strip("_")


def map_tool_names(
    server_name: str, remote_names: Iterable[str]
) -> tuple[tuple[McpToolName, ...], tuple[McpToolNamingIssue, ...]]:
    """生成稳定映射，并排除空名称及同一 Server 内的名称冲突。"""

    grouped: dict[str, list[str]] = {}
    issues: list[McpToolNamingIssue] = []
    for remote_name in sorted(remote_names):
        normalized = normalize_tool_name(remote_name)
        if not normalized:
            issues.append(McpToolNamingIssue(remote_name, "远端工具名称无法规范化"))
            continue
        grouped.setdefault(normalized, []).append(remote_name)

    mappings: list[McpToolName] = []
    for normalized, names in sorted(grouped.items()):
        if len(names) > 1:
            issues.extend(
                McpToolNamingIssue(name, f"远端工具名称规范化冲突：{normalized}") for name in names
            )
            continue
        mappings.append(McpToolName(f"mcp_{server_name}_{normalized}", names[0]))
    return tuple(mappings), tuple(
        sorted(issues, key=lambda issue: (issue.remote_name, issue.message))
    )
