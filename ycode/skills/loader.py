"""标准 SKILL.md 读取与 YCode 扩展校验。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from ycode.skills.models import (
    SkillCatalogEntry,
    SkillConfig,
    SkillContextKind,
    SkillExecutionMode,
    SkillProblem,
    SkillProblemSeverity,
    SkillSnapshot,
    SkillValidationEnvironment,
)

_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_STANDARD_FIELDS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)
_TOOL_ALIASES = {
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Bash": "run_command",
    "PowerShell": "run_command",
    "Glob": "glob",
    "Grep": "grep",
    "ToolSearch": "tool_search",
}
_YCODE_METADATA = frozenset(
    {
        "ycode-visible-tools",
        "ycode-execution-mode",
        "ycode-model",
        "ycode-context",
        "ycode-recent-turns",
        "ycode-argument-hint",
    }
)


class SkillLoader:
    def load(
        self,
        source_path: Path,
        environment: SkillValidationEnvironment,
    ) -> SkillCatalogEntry:
        path = Path(source_path)
        directory_name = path.parent.name
        try:
            raw_bytes = path.read_bytes()
        except OSError:
            return self._unavailable(directory_name, path, "skill_read_failed", "无法读取 SKILL.md")
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return self._unavailable(
                directory_name,
                path,
                "skill_encoding_invalid",
                "SKILL.md 必须使用 UTF-8 编码",
            )

        parsed = self._parse_frontmatter(text, directory_name, path)
        if isinstance(parsed, SkillCatalogEntry):
            return parsed
        metadata, instructions = parsed
        problems: list[SkillProblem] = []
        self._validate_standard(metadata, directory_name, problems)
        if problems:
            return SkillCatalogEntry(directory_name, path, None, tuple(problems))

        name = metadata["name"]
        description = metadata["description"]
        extension = metadata.get("metadata", {})
        config = self._parse_config(metadata, extension, environment, problems)
        if name in environment.builtin_commands:
            problems.append(
                self._error("builtin_command_conflict", f"Skill 名称与内置命令冲突：/{name}")
            )
        if config is None or any(
            problem.severity is SkillProblemSeverity.ERROR for problem in problems
        ):
            return SkillCatalogEntry(directory_name, path, None, tuple(problems))

        snapshot = SkillSnapshot(
            name=name,
            description=description,
            root=path.parent,
            source_path=path,
            instructions=instructions.strip(),
            config=config,
            license=metadata.get("license"),
            compatibility=metadata.get("compatibility"),
            metadata=extension,
            fingerprint=hashlib.sha256(raw_bytes).hexdigest(),
        )
        return SkillCatalogEntry(directory_name, path, snapshot, tuple(problems))

    def _parse_frontmatter(
        self,
        text: str,
        directory_name: str,
        path: Path,
    ) -> tuple[dict[str, Any], str] | SkillCatalogEntry:
        lines = text.splitlines()
        if not lines or lines[0] != "---":
            return self._unavailable(
                directory_name,
                path,
                "frontmatter_missing",
                "SKILL.md 必须以 YAML frontmatter 开始",
            )
        try:
            closing = lines.index("---", 1)
        except ValueError:
            return self._unavailable(
                directory_name,
                path,
                "frontmatter_unclosed",
                "SKILL.md frontmatter 缺少结束分隔符",
            )
        yaml_text = "\n".join(lines[1:closing])
        try:
            metadata = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            return self._unavailable(
                directory_name,
                path,
                "frontmatter_invalid",
                "SKILL.md frontmatter 不是有效 YAML",
            )
        if not isinstance(metadata, dict) or any(not isinstance(key, str) for key in metadata):
            return self._unavailable(
                directory_name,
                path,
                "frontmatter_type_invalid",
                "SKILL.md frontmatter 必须是字符串键映射",
            )
        return metadata, "\n".join(lines[closing + 1 :])

    def _validate_standard(
        self,
        data: dict[str, Any],
        directory_name: str,
        problems: list[SkillProblem],
    ) -> None:
        unknown = sorted(set(data) - _STANDARD_FIELDS)
        if unknown:
            problems.append(
                self._error(
                    "frontmatter_field_unknown", f"未知 frontmatter 字段：{', '.join(unknown)}"
                )
            )
        name = data.get("name")
        if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name) or "--" in name:
            problems.append(self._error("name_invalid", "Skill name 不符合标准名称约束"))
        elif name != directory_name:
            problems.append(self._error("name_directory_mismatch", "Skill name 必须与父目录一致"))
        description = data.get("description")
        if not isinstance(description, str) or not description.strip() or len(description) > 1024:
            problems.append(
                self._error("description_invalid", "Skill description 必须为 1–1024 个字符")
            )
        license_value = data.get("license")
        if license_value is not None and (
            not isinstance(license_value, str) or not license_value.strip()
        ):
            problems.append(self._error("license_invalid", "Skill license 必须是非空字符串"))
        compatibility = data.get("compatibility")
        if compatibility is not None and (
            not isinstance(compatibility, str)
            or not compatibility.strip()
            or len(compatibility) > 500
        ):
            problems.append(
                self._error("compatibility_invalid", "Skill compatibility 必须为 1–500 个字符")
            )
        extension = data.get("metadata", {})
        if not isinstance(extension, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in extension.items()
        ):
            problems.append(self._error("metadata_invalid", "Skill metadata 必须是字符串映射"))
        allowed = data.get("allowed-tools")
        if allowed is not None and not isinstance(allowed, str):
            problems.append(
                self._error("allowed_tools_invalid", "Skill allowed-tools 必须是字符串")
            )

    def _parse_config(
        self,
        data: dict[str, Any],
        extension: dict[str, str],
        environment: SkillValidationEnvironment,
        problems: list[SkillProblem],
    ) -> SkillConfig | None:
        mode_text = extension.get("ycode-execution-mode", SkillExecutionMode.SHARED.value)
        try:
            mode = SkillExecutionMode(mode_text)
        except ValueError:
            problems.append(self._error("execution_mode_invalid", "ycode-execution-mode 无效"))
            return None

        context_default = (
            SkillContextKind.CURRENT.value if mode is SkillExecutionMode.SHARED else ""
        )
        context_text = extension.get("ycode-context", context_default)
        try:
            context = SkillContextKind(context_text)
        except ValueError:
            problems.append(self._error("context_invalid", "ycode-context 无效或缺失"))
            return None

        model_name = extension.get("ycode-model")
        if model_name is not None and model_name not in environment.provider_names:
            problems.append(self._error("model_not_found", f"Skill 模型配置不存在：{model_name}"))

        recent_turns: int | None = None
        recent_text = extension.get("ycode-recent-turns")
        if recent_text is not None:
            try:
                recent_turns = int(recent_text)
            except ValueError:
                problems.append(
                    self._error("recent_turns_invalid", "ycode-recent-turns 必须是正整数")
                )
                return None

        visible_tools = self._parse_tool_list(
            extension.get("ycode-visible-tools"),
            environment,
            problems,
            field_name="ycode-visible-tools",
            allow_expressions=False,
        )
        allowed_tools = self._parse_tool_list(
            data.get("allowed-tools"),
            environment,
            problems,
            field_name="allowed-tools",
            allow_expressions=True,
        )
        if allowed_tools is None:
            allowed_tools = frozenset()
        visible_set = environment.tool_names if visible_tools is None else visible_tools
        outside = sorted(allowed_tools - visible_set)
        if outside:
            problems.append(
                self._error(
                    "allowed_tools_not_visible",
                    f"预批准工具不在可见工具集合：{', '.join(outside)}",
                )
            )
        try:
            return SkillConfig(
                execution_mode=mode,
                model_name=model_name,
                context_kind=context,
                recent_turns=recent_turns,
                visible_tools=visible_tools,
                allowed_tools=allowed_tools,
                argument_hint=extension.get("ycode-argument-hint", ""),
            )
        except (TypeError, ValueError) as error:
            problems.append(self._error("execution_config_invalid", str(error)))
            return None

    def _parse_tool_list(
        self,
        value: object,
        environment: SkillValidationEnvironment,
        problems: list[SkillProblem],
        *,
        field_name: str,
        allow_expressions: bool,
    ) -> frozenset[str] | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            problems.append(self._error("tool_list_invalid", f"{field_name} 必须是非空字符串"))
            return frozenset()
        names: set[str] = set()
        for raw_name in value.split():
            if "(" in raw_name or ")" in raw_name:
                if allow_expressions:
                    problems.append(
                        SkillProblem(
                            "tool_expression_unsupported",
                            f"参数级工具授权未生效：{raw_name}",
                            SkillProblemSeverity.WARNING,
                        )
                    )
                    continue
                problems.append(
                    self._error("tool_expression_invalid", f"可见工具不支持参数表达式：{raw_name}")
                )
                continue
            name = _TOOL_ALIASES.get(raw_name, raw_name)
            if name not in environment.tool_names:
                problems.append(self._error("tool_not_found", f"工具不存在：{raw_name}"))
                continue
            names.add(name)
        return frozenset(names)

    @staticmethod
    def _error(code: str, message: str) -> SkillProblem:
        return SkillProblem(code, message, SkillProblemSeverity.ERROR)

    def _unavailable(
        self,
        directory_name: str,
        path: Path,
        code: str,
        message: str,
    ) -> SkillCatalogEntry:
        return SkillCatalogEntry(directory_name, path, None, (self._error(code, message),))


__all__ = ["SkillLoader"]
