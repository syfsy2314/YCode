"""根据配置创建 Provider。"""

from collections.abc import Callable

from ycode.config.models import ProviderConfig, ProviderProtocol
from ycode.core.provider import ChatProvider
from ycode.providers.anthropic import AnthropicProvider
from ycode.providers.openai import OpenAIProvider

ProviderFactory = Callable[[ProviderConfig], ChatProvider]

_PROVIDERS: dict[ProviderProtocol, ProviderFactory] = {
    ProviderProtocol.ANTHROPIC: AnthropicProvider,
    ProviderProtocol.OPENAI: OpenAIProvider,
}


def create_provider(config: ProviderConfig) -> ChatProvider:
    try:
        factory = _PROVIDERS[config.protocol]
    except KeyError as error:
        raise ValueError(f"不支持的 Provider 协议：{config.protocol}") from error
    return factory(config)
