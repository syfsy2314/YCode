"""YAML 配置对应的强类型模型。"""

from enum import StrEnum
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
