"""项目子 Agent 角色文件的严格解析。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ycode.security.models import PermissionMode
from ycode.subagents.models import (
    SubagentRoleCatalogEntry,
    SubagentRoleConfig,
    SubagentRoleProblem,
    SubagentRoleSnapshot,
    SubagentRoleValidationEnvironment,
)

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_FIELDS = frozenset(
    {
        "name",
        "description",
        "model",
        "allowed-tools",
        "denied-tools",
        "max-rounds",
        "permission",
    }
)


def normalize_role_name(value: str) -> str:
    return value.strip().casefold()


class SubagentRoleLoader:
    def load(
        self,
        source_path: str | Path,
        environment: SubagentRoleValidationEnvironment,
        *,
        builtin: bool = False,
    ) -> SubagentRoleCatalogEntry:
        path = Path(source_path)
        source = str(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return self._unavailable(source, None, "role_read_failed", "无法读取角色文件")
        return self.load_text(
            text,
            source=source,
            file_stem=path.stem,
            environment=environment,
            builtin=builtin,
        )

    def load_text(
        self,
        text: str,
        *,
        source: str,
        file_stem: str,
        environment: SubagentRoleValidationEnvironment,
        builtin: bool = False,
    ) -> SubagentRoleCatalogEntry:
        parsed = self._parse_frontmatter(text, source)
        if isinstance(parsed, SubagentRoleCatalogEntry):
            return parsed
        data, prompt = parsed
        raw_name = data.get("name")
        normalized_name = normalize_role_name(raw_name) if isinstance(raw_name, str) else None
        problems: list[SubagentRoleProblem] = []

        unknown = sorted(set(data) - _FIELDS)
        if unknown:
            problems.append(
                self._problem(
                    source,
                    "frontmatter_field_unknown",
                    f"未知 frontmatter 字段：{', '.join(unknown)}",
                )
            )
        if normalized_name is None or not _NAME_PATTERN.fullmatch(normalized_name):
            problems.append(self._problem(source, "name_invalid", "角色 name 格式无效"))
        elif normalize_role_name(file_stem) != normalized_name:
            problems.append(
                self._problem(source, "name_file_mismatch", "角色 name 必须与文件名一致")
            )

        description = data.get("description")
        if not isinstance(description, str) or not description.strip():
            problems.append(
                self._problem(source, "description_invalid", "角色 description 不能为空")
            )
        if not prompt.strip():
            problems.append(self._problem(source, "prompt_empty", "角色 Markdown 正文不能为空"))

        model = data.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            problems.append(self._problem(source, "model_invalid", "角色 model 必须是非空字符串"))
        elif isinstance(model, str) and model not in environment.provider_names:
            problems.append(
                self._problem(source, "model_not_found", f"角色模型配置不存在：{model}")
            )

        allowed = self._tool_list(data.get("allowed-tools"), "allowed-tools", source, problems)
        denied = self._tool_list(data.get("denied-tools", []), "denied-tools", source, problems)
        for name in sorted((allowed or frozenset()) | denied):
            if name not in environment.tool_names:
                problems.append(self._problem(source, "tool_not_found", f"工具不存在：{name}"))
        overlap = sorted((allowed or frozenset()) & denied)
        if overlap:
            problems.append(
                self._problem(
                    source,
                    "tool_lists_overlap",
                    f"工具同时出现在白名单和黑名单：{', '.join(overlap)}",
                )
            )

        max_rounds = data.get("max-rounds", 10)
        if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 1:
            problems.append(self._problem(source, "max_rounds_invalid", "max-rounds 必须是正整数"))

        permission_text = data.get("permission", PermissionMode.DEFAULT.value)
        try:
            permission = PermissionMode(permission_text)
        except (TypeError, ValueError):
            permission = PermissionMode.DEFAULT
            problems.append(
                self._problem(
                    source,
                    "permission_invalid",
                    "permission 必须是 strict/default/allow",
                )
            )

        if problems:
            return SubagentRoleCatalogEntry(
                source,
                normalized_name,
                None,
                tuple(problems),
            )
        config = SubagentRoleConfig(
            name=normalized_name or "",
            description=description.strip(),
            prompt=prompt.strip(),
            model=model.strip() if isinstance(model, str) else None,
            allowed_tools=allowed,
            denied_tools=denied,
            max_rounds=max_rounds,
            permission=permission,
        )
        return SubagentRoleCatalogEntry(
            source,
            normalized_name,
            SubagentRoleSnapshot(config, source, builtin),
        )

    def _parse_frontmatter(
        self,
        text: str,
        source: str,
    ) -> tuple[dict[str, Any], str] | SubagentRoleCatalogEntry:
        lines = text.splitlines()
        if not lines or lines[0] != "---":
            return self._unavailable(
                source, None, "frontmatter_missing", "角色文件必须以 YAML frontmatter 开始"
            )
        try:
            closing = lines.index("---", 1)
        except ValueError:
            return self._unavailable(
                source, None, "frontmatter_unclosed", "角色 frontmatter 缺少结束分隔符"
            )
        try:
            data = yaml.safe_load("\n".join(lines[1:closing]))
        except yaml.YAMLError:
            return self._unavailable(
                source, None, "frontmatter_invalid", "角色 frontmatter 不是有效 YAML"
            )
        if not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
            return self._unavailable(
                source,
                None,
                "frontmatter_type_invalid",
                "角色 frontmatter 必须是字符串键映射",
            )
        return data, "\n".join(lines[closing + 1 :])

    def _tool_list(
        self,
        value: object,
        field_name: str,
        source: str,
        problems: list[SubagentRoleProblem],
    ) -> frozenset[str] | None:
        if value is None:
            return None
        if not isinstance(value, list) or any(
            not isinstance(name, str) or not name.strip() for name in value
        ):
            problems.append(
                self._problem(source, "tool_list_invalid", f"{field_name} 必须是工具名称列表")
            )
            return frozenset()
        names = [name.strip() for name in value]
        if len(set(names)) != len(names):
            problems.append(
                self._problem(source, "tool_list_duplicate", f"{field_name} 不允许重复")
            )
        return frozenset(names)

    @staticmethod
    def _problem(source: str, code: str, message: str) -> SubagentRoleProblem:
        return SubagentRoleProblem(source, code, message)

    def _unavailable(
        self,
        source: str,
        normalized_name: str | None,
        code: str,
        message: str,
    ) -> SubagentRoleCatalogEntry:
        return SubagentRoleCatalogEntry(
            source,
            normalized_name,
            None,
            (self._problem(source, code, message),),
        )
