"""安全加载并校验 YAML 配置。"""

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ycode.config.models import AppConfig, ProviderConfig
from ycode.errors import ConfigError

_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _expand_active_api_key(data: dict[str, Any], index: int) -> dict[str, Any]:
    expanded = copy.deepcopy(data)
    raw_key = expanded.get("api_key")
    if not isinstance(raw_key, str):
        return expanded

    match = _ENV_REFERENCE.fullmatch(raw_key)
    if match is None:
        return expanded

    variable_name = match.group(1)
    value = os.environ.get(variable_name)
    if value is None:
        raise ConfigError(f"providers[{index}].api_key 引用的环境变量不存在：{variable_name}")
    expanded["api_key"] = value
    return expanded


def _format_validation_error(error: ValidationError, prefix: tuple[str | int, ...] = ()) -> str:
    details: list[str] = []
    for item in error.errors(include_url=False, include_input=False):
        parts = (*prefix, *item["loc"])
        location = ".".join(str(part) for part in parts) or "配置"
        details.append(f"{location}: {item['msg']}")
    return "；".join(details)


def load_config(path: str | Path) -> AppConfig:
    """读取指定 YAML 文件，展开 Key 引用并返回校验后的配置。"""

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(
            f"无法读取配置文件 {config_path}：{error.strerror or '读取失败'}"
        ) from error
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f"（第 {mark.line + 1} 行，第 {mark.column + 1} 列）" if mark else ""
        raise ConfigError(f"YAML 无法解析{location}") from error

    if not isinstance(raw, dict):
        raise ConfigError("配置顶层必须是 YAML 映射")

    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as error:
        raise ConfigError(f"配置校验失败：{_format_validation_error(error)}") from error

    active_index = next(
        index for index, provider in enumerate(config.providers) if provider.name == config.active
    )
    active_data = _expand_active_api_key(config.providers[active_index].as_mapping(), active_index)

    try:
        active_provider = ProviderConfig.model_validate(active_data)
    except ValidationError as error:
        raise ConfigError(
            f"配置校验失败：{_format_validation_error(error, ('providers', active_index))}"
        ) from error

    return config.with_active_provider(active_provider)
