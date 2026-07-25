import pytest

from ycode.config.models import ProviderConfig
from ycode.core.provider import ChatProvider
from ycode.providers.anthropic import AnthropicProvider
from ycode.providers.factory import create_provider
from ycode.providers.openai import OpenAIProvider


@pytest.mark.parametrize(
    ("protocol", "expected_type"),
    [("anthropic", AnthropicProvider), ("openai", OpenAIProvider)],
)
def test_factory_routes_protocol(protocol: str, expected_type: type[object]) -> None:
    config = ProviderConfig.model_validate(
        {
            "name": "local",
            "protocol": protocol,
            "model": "test-model",
            "base_url": "http://localhost:9000/v1",
            "api_key": "placeholder",
        }
    )
    provider = create_provider(config)
    assert isinstance(provider, expected_type)
    assert isinstance(provider, ChatProvider)
