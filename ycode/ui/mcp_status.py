"""MCP 启动摘要与脱敏状态表。"""

from rich.table import Table
from rich.text import Text

from ycode.mcp.models import McpStatusReport


def render_mcp_summary(report: McpStatusReport) -> Text:
    summary = Text()
    summary.append("MCP: ", style="bold")
    summary.append(f"可用 {report.ready_count}")
    summary.append(f" / 失败 {report.failed_count}")
    summary.append(f" / 未启用 {report.disabled_count}")
    if report.security_warnings:
        summary.append(f" / 安全警告 {len(report.security_warnings)}", style="yellow")
    return summary


def render_mcp_status(report: McpStatusReport) -> Table:
    table = Table(title="MCP Servers", expand=True)
    table.add_column("Server")
    table.add_column("Transport")
    table.add_column("State")
    table.add_column("Tools", justify="right")
    table.add_column("Recent error")
    for server in report.servers:
        error = ""
        if server.last_error is not None:
            error = f"{server.last_error.code}: {server.last_error.message}"
        table.add_row(
            server.name,
            server.transport,
            server.state.value,
            str(server.tool_count),
            error,
        )
    for warning in report.security_warnings:
        table.add_row(
            warning.tool_name,
            "security",
            "warning",
            "-",
            f"{warning.code}: {warning.message}",
        )
    return table
