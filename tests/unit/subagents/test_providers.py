from collections.abc import AsyncIterator, Sequence

from pydantic import SecretStr

from ycode.config import ProviderConfig, ProviderProtocol
from ycode.core import AgentModelRequest, ChatMessage, StreamEvent
from ycode.subagents import SubagentProviderPool


def config(name: str, protocol: ProviderProtocol = ProviderProtocol.ANTHROPIC) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol=protocol,
        model=f"{name}-model",
        base_url="https://example.com",
        api_key=SecretStr("secret"),
    )


class FakeAgentProvider:
    def __init__(self) -> None:
        self.close_count = 0

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[StreamEvent]:
        del messages
        if False:
            yield

    async def stream_agent(
        self,
        request: AgentModelRequest,
    ) -> AsyncIterator[StreamEvent]:
        del request
        if False:
            yield

    async def close(self) -> None:
        self.close_count += 1


async def test_pool_borrows_current_and_reuses_owned_named_provider() -> None:
    current = FakeAgentProvider()
    created: list[FakeAgentProvider] = []

    def factory(provider_config: ProviderConfig) -> FakeAgentProvider:
        del provider_config
        provider = FakeAgentProvider()
        created.append(provider)
        return provider

    pool = SubagentProviderPool(
        config("current"),
        current,
        lambda name: config(name),
        factory,
    )

    assert pool.get(None) is current
    assert pool.get("current") is current
    first = pool.get("reviewer")
    assert pool.get("reviewer") is first
    assert len(created) == 1

    await pool.close()
    await pool.close()

    assert current.close_count == 0
    assert created[0].close_count == 1


async def test_pool_rejects_named_openai_provider() -> None:
    current = FakeAgentProvider()
    pool = SubagentProviderPool(
        config("current"),
        current,
        lambda name: config(name, ProviderProtocol.OPENAI),
        lambda item: FakeAgentProvider(),
    )

    try:
        pool.get("openai")
    except ValueError as error:
        assert "Anthropic" in str(error)
    else:
        raise AssertionError("应拒绝 OpenAI 子 Agent Provider")
