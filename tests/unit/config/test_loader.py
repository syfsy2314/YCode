from pathlib import Path

import pytest

from ycode.config.loader import load_config
from ycode.errors import ConfigError


def write_config(path: Path, api_key: str = "plain-key") -> None:
    path.write_text(
        "\n".join(
            [
                "active: local",
                "providers:",
                "  - name: local",
                "    protocol: anthropic",
                "    model: claude-test",
                "    base_url: http://localhost:9000/v1",
                f"    api_key: '{api_key}'",
            ]
        ),
        encoding="utf-8",
    )


def write_config_with_draft(path: Path, *, active: str = "local") -> None:
    path.write_text(
        "\n".join(
            [
                f"active: {active}",
                "providers:",
                "  - name: local",
                "    protocol: anthropic",
                "    model: claude-test",
                "    base_url: http://localhost:9000",
                "    api_key: plain-key",
                "  - name: draft",
                "    protocol: future-protocol",
                "    api_key: ${YCODE_UNUSED_KEY}",
                "    thinking: not-a-boolean",
            ]
        ),
        encoding="utf-8",
    )


def test_loads_plain_api_key(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)
    assert load_config(path).active_provider.api_key.get_secret_value() == "plain-key"


def test_expands_environment_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.yaml"
    write_config(path, "${YCODE_TEST_KEY}")
    monkeypatch.setenv("YCODE_TEST_KEY", "environment-secret")

    config = load_config(path)
    assert config.active_provider.api_key.get_secret_value() == "environment-secret"
    assert "environment-secret" not in path.read_text(encoding="utf-8")


def test_missing_environment_reference_names_variable(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config(path, "${YCODE_MISSING_KEY}")
    with pytest.raises(ConfigError, match="YCODE_MISSING_KEY"):
        load_config(path)


def test_inactive_provider_is_not_fully_validated_or_expanded(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config_with_draft(path)
    original = path.read_text(encoding="utf-8")

    config = load_config(path)

    assert config.active_provider.name == "local"
    assert config.providers[1].as_mapping()["protocol"] == "future-protocol"
    assert path.read_text(encoding="utf-8") == original


def test_switching_to_draft_expands_only_its_environment_reference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    write_config_with_draft(path, active="draft")

    with pytest.raises(ConfigError, match="YCODE_UNUSED_KEY"):
        load_config(path)


@pytest.mark.parametrize(
    ("draft_fields", "expected"),
    [
        (
            "protocol: unsupported\nmodel: x\nbase_url: http://localhost\napi_key: key",
            "protocol",
        ),
        (
            "protocol: openai\nmodel: x\nbase_url: http://localhost\napi_key: key\nthinking: true",
            "anthropic",
        ),
        ("protocol: openai", "model"),
    ],
)
def test_switching_to_invalid_draft_reports_its_error(
    tmp_path: Path,
    draft_fields: str,
    expected: str,
) -> None:
    path = tmp_path / "config.yaml"
    indented_fields = draft_fields.replace("\n", "\n    ")
    path.write_text(
        f"active: draft\nproviders:\n  - name: local\n  - name: draft\n    {indented_fields}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=expected):
        load_config(path)


def test_yaml_error_has_location_without_secret(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("api_key: top-secret\nproviders: [", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "YAML 无法解析" in str(caught.value)
    assert "top-secret" not in str(caught.value)


def test_validation_error_does_not_leak_key(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "active: missing\nproviders:\n  - name: local\n    protocol: invalid\n"
        "    model: x\n    base_url: bad\n    api_key: secret-do-not-print\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "配置校验失败" in str(caught.value)
    assert "secret-do-not-print" not in str(caught.value)


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("- invalid", encoding="utf-8")
    with pytest.raises(ConfigError, match="顶层"):
        load_config(path)
