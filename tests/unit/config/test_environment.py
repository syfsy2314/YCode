import os
from pathlib import Path

import pytest

from ycode.config.environment import EnvironmentResolver, load_project_dotenv
from ycode.core.messages import freeze_json, thaw_json
from ycode.errors import ConfigError


def test_dotenv_missing_file_returns_empty_mapping(tmp_path: Path) -> None:
    assert dict(load_project_dotenv(tmp_path)) == {}


def test_dotenv_disables_recursive_interpolation_and_does_not_mutate_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FROM_DOTENV", raising=False)
    (tmp_path / ".env").write_text("BASE=value\nFROM_DOTENV=${BASE}\n", encoding="utf-8")

    values = load_project_dotenv(tmp_path)

    assert values == {"BASE": "value", "FROM_DOTENV": "${BASE}"}
    assert "FROM_DOTENV" not in os.environ


def test_dotenv_parse_error_does_not_include_file_content(tmp_path: Path) -> None:
    secret = "do-not-disclose"
    (tmp_path / ".env").write_text(f"TOKEN={secret}\ninvalid env line\n", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_project_dotenv(tmp_path)

    assert secret not in str(caught.value)


def test_resolver_prefers_system_environment() -> None:
    resolver = EnvironmentResolver(
        {"TOKEN": "dotenv-token", "OTHER": "dotenv-other"},
        {"TOKEN": "system-token"},
    )

    assert resolver.resolve("TOKEN") == "system-token"
    assert resolver.resolve("OTHER") == "dotenv-other"
    assert resolver.resolve("MISSING") is None


def test_interpolate_supports_embedded_multiple_references() -> None:
    resolver = EnvironmentResolver({"HOST": "example.test", "TOKEN": "secret"}, {})

    result = resolver.interpolate("Bearer ${TOKEN} at https://${HOST}/v1")

    assert result.get_secret_value() == "Bearer secret at https://example.test/v1"


def test_interpolate_reports_only_missing_variable_names() -> None:
    resolver = EnvironmentResolver({}, {})

    with pytest.raises(ConfigError) as caught:
        resolver.interpolate("Bearer ${MISSING} ${ALSO_MISSING}")

    assert "MISSING, ALSO_MISSING" in str(caught.value)
    assert "Bearer" not in str(caught.value)


def test_redactor_replaces_multiple_and_overlapping_secrets() -> None:
    from pydantic import SecretStr

    from ycode.config.environment import SecretRedactor

    redactor = SecretRedactor()
    redactor.add("")
    redactor.add("short")
    redactor.add(SecretStr("shorter-secret"))

    assert redactor.redact_text("shorter-secret and short") == "[REDACTED] and [REDACTED]"


def test_redactor_recursively_replaces_json_strings() -> None:
    from ycode.config.environment import SecretRedactor

    redactor = SecretRedactor()
    redactor.add("top-secret")
    value = freeze_json({"token": "top-secret", "items": ["safe", {"nested": "top-secret"}]})

    assert thaw_json(redactor.redact_json(value)) == {
        "token": "[REDACTED]",
        "items": ["safe", {"nested": "[REDACTED]"}],
    }
