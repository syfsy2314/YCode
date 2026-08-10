"""安全加载并校验 YAML 配置。"""

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ycode.config.discovery import resolve_project_root
from ycode.config.environment import EnvironmentResolver, SecretRedactor, load_project_dotenv
from ycode.config.mcp import LoadedAppConfig, McpConfigSet, load_mcp_servers
from ycode.config.models import AppConfig, ProviderConfig, ProviderProtocol
from ycode.errors import ConfigError

_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _expand_active_api_key(
    data: dict[str, Any], index: int, resolver: EnvironmentResolver | None = None
) -> dict[str, Any]:
    expanded = copy.deepcopy(data)
    raw_key = expanded.get("api_key")
    if not isinstance(raw_key, str):
        return expanded

    match = _ENV_REFERENCE.fullmatch(raw_key)
    if match is None:
        return expanded

    variable_name = match.group(1)
    if resolver is not None:
        try:
            expanded["api_key"] = resolver.interpolate(raw_key)
        except ConfigError as error:
            raise ConfigError(f"providers[{index}].api_key {error}") from error
        return expanded

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


def load_config(path: str | Path) -> LoadedAppConfig:
    """读取指定 YAML 文件并返回包含 MCP 加载结果的配置。"""

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
    active_data = config.providers[active_index].as_mapping()
    redactor = SecretRedactor()
    project_root = resolve_project_root(config_path)
    resolver: EnvironmentResolver | None = None

    if active_data.get("protocol") == ProviderProtocol.ANTHROPIC:
        resolver = EnvironmentResolver(load_project_dotenv(project_root))

    active_data = _expand_active_api_key(active_data, active_index, resolver)

    try:
        active_provider = ProviderConfig.model_validate(active_data)
    except ValidationError as error:
        raise ConfigError(
            f"配置校验失败：{_format_validation_error(error, ('providers', active_index))}"
        ) from error

    redactor.add(active_provider.api_key)
    loaded_app = config.with_active_provider(active_provider)
    mcp = (
        load_mcp_servers(config.mcp_servers, resolver, redactor)
        if resolver is not None
        else McpConfigSet()
    )
    return LoadedAppConfig(
        app=loaded_app,
        active_provider=active_provider,
        project_root=project_root,
        mcp=mcp,
        redactor=redactor,
    )


def load_named_anthropic_provider(
    loaded: LoadedAppConfig,
    name: str,
) -> ProviderConfig:
    """按需校验 Skill 引用的已有 Anthropic Provider。"""

    if loaded.active_provider.name == name:
        if loaded.active_provider.protocol is not ProviderProtocol.ANTHROPIC:
            raise ConfigError(f"Skill 模型配置不是 Anthropic Provider：{name}")
        return loaded.active_provider
    try:
        index, entry = next(
            (index, entry) for index, entry in enumerate(loaded.app.providers) if entry.name == name
        )
    except StopIteration as error:
        raise ConfigError(f"Skill 模型配置不存在：{name}") from error
    data = entry.as_mapping()
    if data.get("protocol") != ProviderProtocol.ANTHROPIC:
        raise ConfigError(f"Skill 模型配置不是 Anthropic Provider：{name}")
    resolver = EnvironmentResolver(load_project_dotenv(loaded.project_root))
    data = _expand_active_api_key(data, index, resolver)
    try:
        provider = ProviderConfig.model_validate(data)
    except ValidationError as error:
        raise ConfigError(
            f"Skill 模型配置校验失败：{_format_validation_error(error, ('providers', index))}"
        ) from error
    if provider.protocol is not ProviderProtocol.ANTHROPIC:
        raise ConfigError(f"Skill 模型配置不是 Anthropic Provider：{name}")
    loaded.redactor.add(provider.api_key)
    return provider
