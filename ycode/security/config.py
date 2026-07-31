"""项目安全配置发现、加载与校验。"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from ycode.errors import ConfigError
from ycode.security.models import SecurityConfig
from ycode.tools.registry import ToolRegistry

SECURITY_RELATIVE_PATH = Path(".ycode") / "security.yaml"


def discover_security_config(start_dir: str | Path) -> Path | None:
    start = Path(start_dir).expanduser().resolve()
    if not start.is_dir():
        raise ConfigError(f"安全配置搜索起点不是目录：{start}")
    for directory in (start, *start.parents):
        candidate = directory / SECURITY_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    return None


def load_security_config(start_dir: str | Path, registry: ToolRegistry) -> SecurityConfig:
    path = discover_security_config(start_dir)
    if path is None:
        return SecurityConfig()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"无法读取安全配置 {path}。") from error
    except yaml.YAMLError as error:
        raise ConfigError("安全配置 YAML 无法解析。") from error
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("安全配置顶层必须是 YAML 映射")
    try:
        config = SecurityConfig.model_validate(raw)
    except ValidationError as error:
        details = "；".join(
            _format_validation_error(item)
            for item in error.errors(include_url=False, include_input=False)
        )
        raise ConfigError(f"安全配置校验失败：{details}") from error

    for rule in config.rules:
        tool = registry.get(rule.tool)
        if tool is None:
            raise ConfigError(f"安全规则 {rule.id} 引用了未知工具：{rule.tool}")
        fields = tool.definition.arguments_model.model_fields
        unknown = sorted(set(rule.arguments) - set(fields))
        if unknown:
            raise ConfigError(f"安全规则 {rule.id} 引用了未知参数：{', '.join(unknown)}")
    return config


def _format_validation_error(item: dict[str, object]) -> str:
    location = ".".join(str(part) for part in item.get("loc", ())) or "配置"
    error_type = item.get("type")
    if error_type == "extra_forbidden":
        return f"{location}: 不允许额外字段"
    message = str(item.get("msg", "字段无效"))
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    return f"{location}: {message}"
