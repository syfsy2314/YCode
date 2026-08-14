"""项目 Hook 配置发现与容错加载。"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from ycode.hooks.models import (
    HookConfigLoadResult,
    HookDiagnostic,
    HookRule,
    HttpHookAction,
    ShellHookAction,
)

HOOKS_RELATIVE_PATH = Path(".ycode") / "hooks.yaml"


def format_hook_diagnostic(diagnostic: HookDiagnostic) -> str:
    location = diagnostic.path
    if diagnostic.rule_index is not None:
        location += f"，规则 #{diagnostic.rule_index + 1}"
    if diagnostic.rule_id:
        location += f" ({diagnostic.rule_id})"
    return f"Hook 配置错误：{location}：{diagnostic.message}"


def discover_hook_config(start_dir: str | Path) -> Path | None:
    start = Path(start_dir).expanduser().resolve()
    if not start.is_dir():
        return None
    for directory in (start, *start.parents):
        candidate = directory / HOOKS_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    return None


def load_hook_config(start_dir: str | Path) -> HookConfigLoadResult:
    path = discover_hook_config(start_dir)
    if path is None:
        return HookConfigLoadResult()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        return HookConfigLoadResult(
            diagnostics=(_diagnostic("hook_config_parse_error", path, None, "", str(error)),)
        )
    if not isinstance(raw, dict) or not isinstance(raw.get("hooks", []), list):
        return HookConfigLoadResult(
            diagnostics=(
                _diagnostic(
                    "hook_config_structure_error",
                    path,
                    None,
                    "",
                    "Hook 配置顶层必须是包含 hooks 列表的映射",
                ),
            )
        )
    if set(raw) != {"hooks"}:
        return HookConfigLoadResult(
            diagnostics=(
                _diagnostic(
                    "hook_config_structure_error",
                    path,
                    None,
                    "",
                    "Hook 配置顶层不允许 hooks 以外的字段",
                ),
            )
        )

    rules: list[HookRule] = []
    diagnostics: list[HookDiagnostic] = []
    seen: set[str] = set()
    external = False
    for index, item in enumerate(raw["hooks"]):
        rule_id = str(item.get("id", "")) if isinstance(item, dict) else ""
        try:
            rule = HookRule.model_validate(item)
        except ValidationError as error:
            details = "; ".join(_format_error(entry) for entry in error.errors(include_url=False))
            diagnostics.append(_diagnostic("hook_rule_invalid", path, index, rule_id, details))
            continue
        if rule.id in seen:
            diagnostics.append(
                _diagnostic(
                    "hook_rule_duplicate",
                    path,
                    index,
                    rule.id,
                    f"Hook 规则 ID 重复：{rule.id}",
                )
            )
            continue
        seen.add(rule.id)
        rules.append(rule)
        if rule.enabled and isinstance(rule.action, ShellHookAction | HttpHookAction):
            external = True
    return HookConfigLoadResult(tuple(rules), tuple(diagnostics), external)


def _diagnostic(
    code: str,
    path: Path,
    index: int | None,
    rule_id: str,
    message: str,
) -> HookDiagnostic:
    return HookDiagnostic(code, str(path), index, rule_id, message)


def _format_error(item: dict[str, object]) -> str:
    location = ".".join(str(part) for part in item.get("loc", ())) or "rule"
    message = str(item.get("msg", "字段无效")).removeprefix("Value error, ")
    return f"{location}: {message}"
