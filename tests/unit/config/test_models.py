import pytest
from pydantic import ValidationError

from ycode.config.models import AppConfig, ProviderConfig, ProviderEntry, ProviderProtocol


def provider_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "name": "local",
        "protocol": "anthropic",
        "model": "claude-test",
        "base_url": "http://127.0.0.1:8000/v1/",
        "api_key": "super-secret",
    }
    data.update(overrides)
    return data


def test_provider_normalizes_url_and_hides_secret() -> None:
    provider = ProviderConfig.model_validate(provider_data())

    assert provider.protocol is ProviderProtocol.ANTHROPIC
    assert provider.base_url == "http://127.0.0.1:8000/v1"
    assert provider.api_key.get_secret_value() == "super-secret"
    assert "super-secret" not in repr(provider)


def test_app_config_selects_active_provider() -> None:
    config = AppConfig.model_validate(
        {
            "active": "openai-local",
            "providers": [
                {"name": "draft", "protocol": "future"},
                provider_data(
                    name="openai-local", protocol="openai", model="gpt-test", thinking=False
                ),
            ],
        }
    )
    assert config.active_entry.name == "openai-local"

    active_provider = ProviderConfig.model_validate(config.active_entry.as_mapping())
    loaded = config.with_active_provider(active_provider)

    assert loaded.active_provider.name == "openai-local"
    assert loaded.providers[0].as_mapping()["protocol"] == "future"


def test_provider_entry_only_validates_name() -> None:
    entry = ProviderEntry.model_validate(
        {
            "name": "draft",
            "protocol": "not-supported-yet",
            "api_key": "${MISSING_ENV}",
            "thinking": "not-a-boolean",
        }
    )

    assert entry.name == "draft"
    assert entry.as_mapping()["protocol"] == "not-supported-yet"


def test_active_provider_is_unavailable_before_full_validation() -> None:
    config = AppConfig.model_validate({"active": "draft", "providers": [{"name": "draft"}]})

    with pytest.raises(RuntimeError, match="Provider"):
        _ = config.active_provider


@pytest.mark.parametrize(
    "data, expected",
    [
        ({"providers": [{"name": "local"}]}, "active"),
        ({"active": "missing", "providers": [provider_data()]}, "active"),
        (
            {"active": "local", "providers": [provider_data(), provider_data()]},
            "providers.name",
        ),
        ({"active": "local", "providers": [{"protocol": "openai"}]}, "name"),
        ({"active": "local", "providers": [{"name": "   "}]}, "name"),
        ({"active": "local", "providers": {"name": "local"}}, "providers"),
    ],
)
def test_cross_field_validation(data: dict[str, object], expected: str) -> None:
    with pytest.raises(ValidationError, match=expected):
        AppConfig.model_validate(data)


def test_invalid_base_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="http/https"):
        ProviderConfig.model_validate(provider_data(base_url="not-a-url"))


def test_active_openai_rejects_thinking() -> None:
    with pytest.raises(ValidationError, match="anthropic"):
        ProviderConfig.model_validate(
            provider_data(name="openai-local", protocol="openai", thinking=True)
        )
