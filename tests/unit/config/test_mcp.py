import pytest
from pydantic import TypeAdapter, ValidationError

from ycode.config.environment import EnvironmentResolver, SecretRedactor
from ycode.config.mcp import (
    HttpMcpServerConfig,
    McpServerConfig,
    StdioMcpServerConfig,
    load_mcp_servers,
)


def test_model_defaults_and_valid_stdio_config() -> None:
    config = StdioMcpServerConfig.model_validate(
        {"name": "local_tools", "transport": "stdio", "command": "python"}
    )

    assert config.enabled is True
    assert config.args == ()
    assert config.startup_timeout_seconds == 5.0
    assert config.tool_timeout_seconds == 60.0


def test_model_accepts_streamable_http_config() -> None:
    config = TypeAdapter(McpServerConfig).validate_python(
        {
            "name": "remote_tools",
            "transport": "streamable_http",
            "url": "https://mcp.example.test/mcp",
            "headers": {"Authorization": "Bearer token"},
        }
    )

    assert isinstance(config, HttpMcpServerConfig)
    assert config.headers["Authorization"].get_secret_value() == "Bearer token"


def test_explicit_startup_timeout_overrides_default() -> None:
    config = StdioMcpServerConfig.model_validate(
        {
            "name": "local_tools",
            "transport": "stdio",
            "command": "python",
            "startup_timeout_seconds": 10,
        }
    )

    assert config.startup_timeout_seconds == 10.0


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"name": "Bad-Name", "transport": "stdio", "command": "python"}, "name"),
        ({"name": "tools", "transport": "stdio", "command": ""}, "command"),
        (
            {"name": "tools", "transport": "stdio", "command": "python", "env": {"BAD-NAME": "x"}},
            "env",
        ),
        (
            {"name": "tools", "transport": "stdio", "command": "python", "tool_timeout_seconds": 0},
            "tool_timeout_seconds",
        ),
        ({"name": "tools", "transport": "streamable_http", "url": "ftp://example.test"}, "url"),
        (
            {
                "name": "tools",
                "transport": "streamable_http",
                "url": "https://example.test",
                "headers": {"X-Test": "bad\nvalue"},
            },
            "headers",
        ),
    ],
)
def test_model_rejects_invalid_fields(data: dict[str, object], expected: str) -> None:
    with pytest.raises(ValidationError, match=expected):
        TypeAdapter(McpServerConfig).validate_python(data)


def test_isolation_keeps_valid_server_when_another_is_invalid() -> None:
    loaded = load_mcp_servers(
        [
            {"name": "valid", "transport": "stdio", "command": "python"},
            {"name": "bad", "transport": "stdio", "command": ""},
        ],
        EnvironmentResolver({}, {}),
        SecretRedactor(),
    )

    assert [server.name for server in loaded.servers] == ["valid"]
    assert loaded.issues[0].entry_index == 1
    assert loaded.issues[0].code == "invalid_config"


def test_duplicate_names_exclude_every_matching_entry() -> None:
    loaded = load_mcp_servers(
        [
            {"name": "same", "transport": "stdio", "command": "python"},
            {"name": "same", "transport": "stdio", "command": "node"},
        ],
        EnvironmentResolver({}, {}),
        SecretRedactor(),
    )

    assert loaded.servers == ()
    assert [issue.code for issue in loaded.issues] == ["duplicate_name", "duplicate_name"]


def test_enabled_server_interpolates_secrets_and_disabled_server_does_not() -> None:
    redactor = SecretRedactor()
    loaded = load_mcp_servers(
        [
            {
                "name": "enabled",
                "transport": "stdio",
                "command": "python",
                "env": {"TOKEN": "${TOKEN}"},
            },
            {
                "name": "disabled",
                "enabled": False,
                "transport": "streamable_http",
                "url": "https://example.test/mcp",
                "headers": {"Authorization": "Bearer ${MISSING}"},
            },
        ],
        EnvironmentResolver({"TOKEN": "secret"}, {}),
        redactor,
    )

    assert len(loaded.servers) == 2
    assert loaded.servers[0].env["TOKEN"].get_secret_value() == "secret"
    assert loaded.servers[1].headers["Authorization"].get_secret_value() == "Bearer ${MISSING}"
    assert redactor.redact_text("secret") == "[REDACTED]"


def test_missing_enabled_secret_is_isolated_without_disclosing_value() -> None:
    loaded = load_mcp_servers(
        [
            {
                "name": "private",
                "transport": "stdio",
                "command": "python",
                "env": {"TOKEN": "Bearer ${MISSING}"},
            }
        ],
        EnvironmentResolver({}, {}),
        SecretRedactor(),
    )

    assert loaded.servers == ()
    assert loaded.issues[0].code == "missing_environment_variable"
    assert "MISSING" in loaded.issues[0].message
    assert "Bearer" not in loaded.issues[0].message
