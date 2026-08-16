"""子 Agent 使用的 Anthropic Provider 借用与会话池。"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from ycode.config.models import ProviderConfig, ProviderProtocol
from ycode.core.provider import AgentChatProvider, ChatProvider


class SubagentProviderPool:
    def __init__(
        self,
        current_config: ProviderConfig,
        current_provider: AgentChatProvider,
        named_provider_loader: Callable[[str], ProviderConfig],
        provider_factory: Callable[[ProviderConfig], ChatProvider],
    ) -> None:
        if current_config.protocol is not ProviderProtocol.ANTHROPIC:
            raise ValueError("子 Agent Provider 池仅支持 Anthropic")
        self._current_config = current_config
        self._current_provider = current_provider
        self._named_provider_loader = named_provider_loader
        self._provider_factory = provider_factory
        self._named: dict[str, AgentChatProvider] = {}
        self._closed = False

    def get(self, model_name: str | None) -> AgentChatProvider:
        if self._closed:
            raise RuntimeError("子 Agent Provider 池已关闭")
        if model_name is None or model_name == self._current_config.name:
            return self._current_provider
        if model_name in self._named:
            return self._named[model_name]
        config = self._named_provider_loader(model_name)
        if config.protocol is not ProviderProtocol.ANTHROPIC:
            raise ValueError(f"子 Agent 模型配置不是 Anthropic：{model_name}")
        provider = self._provider_factory(config)
        if not isinstance(provider, AgentChatProvider):
            raise TypeError("子 Agent Provider 不支持结构化 Agent 请求")
        selected = cast(AgentChatProvider, provider)
        self._named[model_name] = selected
        return selected

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        providers = tuple(self._named.values())
        self._named.clear()
        for provider in providers:
            await provider.close()
