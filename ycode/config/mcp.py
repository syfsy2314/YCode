"""MCP Server 配置模型与加载结果。"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from ycode.config.environment import EnvironmentResolver, SecretRedactor
from ycode.config.models import AppConfig, ProviderConfig
from ycode.errors import ConfigError

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SERVER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class _McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    name: str = Field(min_length=1)
    enabled: bool = True
    startup_timeout_seconds: float = Field(default=5.0, gt=0)
    tool_timeout_seconds: float = Field(default=60.0, gt=0)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SERVER_NAME.fullmatch(value):
            raise ValueError("必须匹配 ^[a-z][a-z0-9_]*$")
        return value


class StdioMcpServerConfig(_McpServerConfig):
    transport: Literal["stdio"]
    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    env: Mapping[str, SecretStr] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_env_names(self) -> "StdioMcpServerConfig":
        invalid_names = sorted(name for name in self.env if not _ENVIRONMENT_NAME.fullmatch(name))
        if invalid_names:
            raise ValueError(f"env 名称无效：{', '.join(invalid_names)}")
        return self


class HttpMcpServerConfig(_McpServerConfig):
    transport: Literal["streamable_http"]
    url: str = Field(min_length=1)
    headers: Mapping[str, SecretStr] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("必须是有效的 http/https URL")
        return value

    @model_validator(mode="after")
    def validate_headers(self) -> "HttpMcpServerConfig":
        if any("\n" in name or "\r" in name for name in self.headers):
            raise ValueError("headers 名称不能包含换行")
        if any(
            "\n" in value.get_secret_value() or "\r" in value.get_secret_value()
            for value in self.headers.values()
        ):
            raise ValueError("headers 值不能包含换行")
        return self


McpServerConfig = Annotated[
    StdioMcpServerConfig | HttpMcpServerConfig,
    Field(discriminator="transport"),
]
_MCP_SERVER_CONFIG_ADAPTER = TypeAdapter(McpServerConfig)


@dataclass(frozen=True, slots=True)
class McpConfigIssue:
    entry_index: int
    server_name: str | None
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.entry_index < 0:
            raise ValueError("MCP 配置条目索引不能为负数")
        if not self.code or not self.message:
            raise ValueError("MCP 配置问题必须包含错误码和消息")


@dataclass(frozen=True, slots=True)
class McpConfigSet:
    servers: tuple[McpServerConfig, ...] = ()
    issues: tuple[McpConfigIssue, ...] = ()
    entry_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        indices = self.entry_indices or tuple(range(len(self.servers)))
        if len(indices) != len(self.servers):
            raise ValueError("MCP Server 与配置索引数量不一致")
        object.__setattr__(self, "entry_indices", indices)


@dataclass(frozen=True, slots=True)
class LoadedAppConfig:
    app: AppConfig
    active_provider: ProviderConfig
    project_root: Path
    mcp: McpConfigSet
    redactor: SecretRedactor


def load_mcp_servers(
    entries: list[Any], resolver: EnvironmentResolver, redactor: SecretRedactor
) -> McpConfigSet:
    """独立校验每个 MCP 条目，保留其他 Server 的可用配置。"""

    duplicate_names = _duplicate_names(entries)
    servers: list[McpServerConfig] = []
    entry_indices: list[int] = []
    issues: list[McpConfigIssue] = []

    for index, entry in enumerate(entries):
        server_name = entry.get("name") if isinstance(entry, Mapping) else None
        if isinstance(server_name, str) and server_name in duplicate_names:
            issues.append(
                McpConfigIssue(
                    index, server_name, "duplicate_name", f"MCP Server 名称重复：{server_name}"
                )
            )
            continue
        if not isinstance(entry, Mapping):
            issues.append(
                McpConfigIssue(index, None, "invalid_config", "MCP Server 配置必须是映射")
            )
            continue

        try:
            config = _MCP_SERVER_CONFIG_ADAPTER.validate_python(entry)
            if config.enabled:
                config = _resolve_server_secrets(config, resolver)
            _register_server_secrets(config, redactor)
        except ValidationError as error:
            issues.append(
                McpConfigIssue(
                    index,
                    server_name if isinstance(server_name, str) else None,
                    "invalid_config",
                    _format_validation_error(error),
                )
            )
            continue
        except ConfigError as error:
            issues.append(
                McpConfigIssue(
                    index,
                    server_name if isinstance(server_name, str) else None,
                    "missing_environment_variable",
                    str(error),
                )
            )
            continue

        servers.append(config)
        entry_indices.append(index)

    return McpConfigSet(tuple(servers), tuple(issues), tuple(entry_indices))


def _duplicate_names(entries: list[Any]) -> set[str]:
    names = [entry.get("name") for entry in entries if isinstance(entry, Mapping)]
    return {name for name in names if isinstance(name, str) and names.count(name) > 1}


def _resolve_server_secrets(
    config: McpServerConfig, resolver: EnvironmentResolver
) -> McpServerConfig:
    raw_config = config.model_dump(mode="python")
    if isinstance(config, StdioMcpServerConfig):
        raw_config["env"] = {
            name: resolver.interpolate(value.get_secret_value())
            for name, value in config.env.items()
        }
    else:
        raw_config["headers"] = {
            name: resolver.interpolate(value.get_secret_value())
            for name, value in config.headers.items()
        }
    return _MCP_SERVER_CONFIG_ADAPTER.validate_python(raw_config)


def _register_server_secrets(config: McpServerConfig, redactor: SecretRedactor) -> None:
    secrets = (
        config.env.values() if isinstance(config, StdioMcpServerConfig) else config.headers.values()
    )
    for value in secrets:
        redactor.add(value)


def _format_validation_error(error: ValidationError) -> str:
    details: list[str] = []
    for item in error.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in item["loc"]) or "配置"
        details.append(f"{location}: {item['msg']}")
    return "；".join(details)
