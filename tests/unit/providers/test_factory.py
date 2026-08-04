import json
import subprocess
import sys

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


def test_anthropic_factory_does_not_import_openai_in_fresh_process() -> None:
    script = """
import json
import sys
from ycode.config.models import ProviderConfig
from ycode.providers.factory import create_provider

config = ProviderConfig.model_validate({
    "name": "local",
    "protocol": "anthropic",
    "model": "test-model",
    "base_url": "http://localhost:9000",
    "api_key": "placeholder",
})
create_provider(config)
print(json.dumps({
    "anthropic": "anthropic" in sys.modules,
    "openai": "openai" in sys.modules,
    "openai_provider": "ycode.providers.openai" in sys.modules,
}))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )

    assert json.loads(result.stdout) == {
        "anthropic": True,
        "openai": False,
        "openai_provider": False,
    }
