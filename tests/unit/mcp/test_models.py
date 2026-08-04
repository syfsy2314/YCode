import pytest

from ycode.mcp.models import (
    McpConnectionState,
    McpErrorSummary,
    McpServerStatus,
    McpStatusReport,
)


def test_status_report_counts_states_in_config_order() -> None:
    report = McpStatusReport(
        (
            McpServerStatus("ready", "stdio", McpConnectionState.READY, 2),
            McpServerStatus("starting", "stdio", McpConnectionState.STARTING, 0),
            McpServerStatus("reconnecting", "stdio", McpConnectionState.RECONNECTING, 0),
            McpServerStatus("disabled", "stdio", McpConnectionState.DISABLED, 0),
            McpServerStatus(
                "failed",
                "streamable_http",
                McpConnectionState.UNAVAILABLE,
                0,
                McpErrorSummary("startup_failed", "连接失败"),
            ),
        )
    )

    assert [status.name for status in report.servers] == [
        "ready",
        "starting",
        "reconnecting",
        "disabled",
        "failed",
    ]
    assert report.ready_count == 1
    assert report.starting_count == 2
    assert report.disabled_count == 1
    assert report.failed_count == 1


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: McpErrorSummary("", "message"), "错误码"),
        (lambda: McpServerStatus("server", "stdio", McpConnectionState.READY, -1), "数量"),
    ],
)
def test_status_models_reject_empty_or_invalid_values(factory: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()  # type: ignore[operator]
