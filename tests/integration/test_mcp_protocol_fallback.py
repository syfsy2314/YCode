import json
import sys
from pathlib import Path

import pytest

from ycode.config.environment import SecretRedactor
from ycode.config.mcp import StdioMcpServerConfig
from ycode.mcp.connection import McpConnection

SERVER_PATH = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"


def config(tmp_path: Path, *, legacy: bool) -> StdioMcpServerConfig:
    arguments = [str(SERVER_PATH)]
    if legacy:
        arguments.append("--legacy")
    return StdioMcpServerConfig.model_validate(
        {
            "name": "protocol",
            "transport": "stdio",
            "command": sys.executable,
            "args": arguments,
            "env": {"YCODE_MCP_STATE_FILE": str(tmp_path / "state.jsonl")},
            "startup_timeout_seconds": 5,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("legacy", "expected_version"),
    [(False, "2026-07-28"), (True, "2025-11-25")],
)
async def test_auto_mode_negotiates_modern_and_legacy_with_same_config_shape(
    tmp_path: Path, legacy: bool, expected_version: str
) -> None:
    connection = McpConnection(config(tmp_path, legacy=legacy), SecretRedactor())

    discovery = await connection.start()

    assert discovery.protocol_version == expected_version
    assert any(tool.remote_name == "echo" for tool in discovery.tools)
    assert "version" not in config(tmp_path, legacy=legacy).model_fields_set
    await connection.close()
    if legacy:
        state = [
            json.loads(line)
            for line in (tmp_path / "state.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        initialized = next(item for item in state if item["event"] == "initialize")
        assert initialized["capabilities"] == {}
        reverse = next(item for item in state if item["event"] == "client_response")
        assert reverse == {
            "event": "client_response",
            "pid": state[0]["pid"],
            "has_error": True,
            "has_result": False,
        }
