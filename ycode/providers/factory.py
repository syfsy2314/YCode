"""根据配置创建 Provider。"""

from collections.abc import Callable

from ycode.config.models import ProviderConfig, ProviderProtocol
from ycode.core.provider import ChatProvider

ProviderFactory = Callable[[ProviderConfig], ChatProvider]


def create_provider(config: ProviderConfig) -> ChatProvider:
    if config.protocol is ProviderProtocol.ANTHROPIC:
        from ycode.providers.anthropic import AnthropicProvider

        return AnthropicProvider(config)
    if config.protocol is ProviderProtocol.OPENAI:
        from ycode.providers.openai import OpenAIProvider

        return OpenAIProvider(config)
    raise ValueError(f"不支持的 Provider 协议：{config.protocol}")
