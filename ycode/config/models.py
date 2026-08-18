"""YAML 配置对应的强类型模型。"""

import re
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    field_validator,
    model_validator,
)

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_ASYNC_ALLOWED_TOOLS = (
    "read_file",
    "glob",
    "grep",
    "tool_search",
    "write_file",
    "edit_file",
    "run_command",
)


def _validate_worktree_paths(
    value: tuple[str, ...],
    *,
    allow_glob: bool,
) -> tuple[str, ...]:
    if len(set(value)) != len(value):
        raise ValueError("Worktree 路径不允许重复")
    for item in value:
        if not item or "\\" in item:
            raise ValueError("Worktree 路径必须使用非空仓库相对 POSIX 路径")
        windows = PureWindowsPath(item)
        path = PurePosixPath(item)
        if path.is_absolute() or windows.is_absolute() or windows.drive:
            raise ValueError("Worktree 路径不能是绝对路径")
        if any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("Worktree 路径不能包含空段、. 或 ..")
        if not allow_glob and any(character in item for character in "*?[]"):
            raise ValueError("Worktree 普通路径不能包含 Glob 字符")
    return value


class WorktreeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    copy_files: tuple[str, ...] = ()
    ignored_file_globs: tuple[str, ...] = ()
    link_directories: tuple[str, ...] = ()
    cleanup_ttl_hours: int = Field(default=24, strict=True, gt=0)

    @field_validator("copy_files", "link_directories")
    @classmethod
    def validate_plain_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_worktree_paths(value, allow_glob=False)

    @field_validator("ignored_file_globs")
    @classmethod
    def validate_glob_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_worktree_paths(value, allow_glob=True)


class SubagentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_concurrent: int = Field(default=4, strict=True, gt=0)
    async_allowed_tools: tuple[str, ...] = DEFAULT_ASYNC_ALLOWED_TOOLS

    @field_validator("async_allowed_tools")
    @classmethod
    def validate_async_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("async_allowed_tools 不允许重复")
        if any(not _TOOL_NAME_PATTERN.fullmatch(name) for name in value):
            raise ValueError("async_allowed_tools 必须包含合法工具名称")
        return value


class ProviderProtocol(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class ProviderEntry(BaseModel):
    """只校验配置名称，并保留未激活条目的原始字段。"""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    name: str = Field(min_length=1)

    def as_mapping(self) -> dict[str, Any]:
        return self.model_dump(round_trip=True)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    protocol: ProviderProtocol
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: SecretStr
    thinking: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("必须是有效的 http/https URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_thinking_protocol(self) -> "ProviderConfig":
        if self.protocol is ProviderProtocol.OPENAI and self.thinking:
            raise ValueError("thinking: true 仅适用于 anthropic 协议")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    active: str = Field(min_length=1)
    providers: list[ProviderEntry] = Field(min_length=1)
    mcp_servers: list[Any] = Field(default_factory=list)
    subagents: SubagentConfig = Field(default_factory=SubagentConfig)
    worktrees: WorktreeConfig = Field(default_factory=WorktreeConfig)
    context_window_tokens: int = Field(default=200_000, strict=True, gt=33_000)
    _active_provider: ProviderConfig | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def validate_provider_names(self) -> "AppConfig":
        names = [provider.name for provider in self.providers]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"providers.name 重复：{', '.join(duplicates)}")
        if self.active not in names:
            raise ValueError(f"active 指向不存在的配置：{self.active}")
        return self

    @property
    def active_entry(self) -> ProviderEntry:
        return next(provider for provider in self.providers if provider.name == self.active)

    @property
    def active_provider(self) -> ProviderConfig:
        if self._active_provider is None:
            raise RuntimeError("活动 Provider 尚未完成校验")
        return self._active_provider

    def with_active_provider(self, provider: ProviderConfig) -> "AppConfig":
        if provider.name != self.active:
            raise ValueError("活动 Provider 名称与 active 不一致")
        loaded = self.model_copy(deep=True)
        loaded._active_provider = provider
        return loaded
