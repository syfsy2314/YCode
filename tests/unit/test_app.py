from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.agent import TurnMessage
from ycode.app import run_app
from ycode.config.models import ProviderConfig
from ycode.core import ChatMessage, StopReason, StreamEnd, TextDelta
from ycode.errors import ConfigError
from ycode.mcp.models import McpConnectionState, McpServerStatus, McpStatusReport
from ycode.session import SessionManager
from ycode.tools import (
    JsonSchemaToolArguments,
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)


def write_config(path: Path, protocol: str = "openai") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"active: local\nproviders:\n  - name: local\n    protocol: {protocol}\n"
        "    model: gpt-test\n    base_url: http://localhost:9000/v1\n"
        "    api_key: placeholder\n"
        "  - name: unfinished\n"
        "    protocol: future\n"
        "    api_key: ${YCODE_UNUSED_APP_KEY}\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_app_assembles_components_and_always_closes(tmp_path: Path) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path)
    provider = FakeProvider([])
    seen: dict[str, object] = {}
    factory_configs: list[ProviderConfig] = []

    def provider_factory(config: ProviderConfig) -> FakeProvider:
        factory_configs.append(config)
        return provider

    class FakeUI:
        def __init__(self, config: object, session: object) -> None:
            seen["config"] = config
            seen["session"] = session

        async def run(self) -> None:
            seen["ran"] = True

    await run_app(
        start_dir=tmp_path,
        provider_factory=provider_factory,
        ui_factory=FakeUI,
    )

    assert seen["ran"] is True
    assert factory_configs == [seen["config"]]
    assert factory_configs[0].name == "local"
    assert provider.closed is True
    assert not (tmp_path / ".ycode" / "context").exists()


@pytest.mark.asyncio
async def test_app_assembles_anthropic_agent_with_builtin_tools(tmp_path: Path) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path, "anthropic")
    provider = FakeProvider([[TextDelta(0, "done"), StreamEnd(StopReason.END_TURN)]])
    sessions = []

    class FakeUI:
        def __init__(self, config: object, session: object) -> None:
            self._session = session
            sessions.append(session)

        async def run(self) -> None:
            async for _ in self._session.stream_reply("hello"):
                pass

    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: provider,
        ui_factory=FakeUI,
    )

    request = provider.agent_requests[0]
    assert [definition.name for definition in request.tools] == [
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
        "glob",
        "grep",
        "load_skill",
        "install_skill",
    ]
    assert any(f"Workspace: {tmp_path}" in item for item in request.supplements)
    assert any(
        "<tool_state>" in item and "permission mode: default" in item
        for item in request.supplements
    )
    assert all("Workspace:" not in block for block in request.system_prompt)
    assert all("permission mode:" not in block for block in request.system_prompt)
    assert provider.closed is True
    assert [item.name for item in sessions[0].command_runtime.registry.definitions] == [
        "help",
        "exit",
        "plan",
        "agent",
        "mcp",
        "compact",
        "permission",
        "resume",
        "skills",
        "clear",
    ]
    context_root = tmp_path / ".ycode" / "context"
    assert context_root.is_dir()
    assert list(context_root.iterdir()) == []


@pytest.mark.asyncio
async def test_anthropic_injects_project_instruction_and_memory_index(tmp_path: Path) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path, "anthropic")
    (tmp_path / "YCODE.md").write_text("Use Python 3.12.", encoding="utf-8")
    memory = tmp_path / ".ycode" / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text(
        "- [技术栈](project-stack.md) — Python 版本\n", encoding="utf-8"
    )
    (memory / "project-stack.md").write_text(
        "---\nname: 技术栈\ndescription: Python 版本\ntype: project_knowledge\n---\nPython 3.12\n",
        encoding="utf-8",
    )
    provider = FakeProvider([[TextDelta(0, "done"), StreamEnd(StopReason.END_TURN)]])

    class FakeUI:
        def __init__(self, config: object, session: object) -> None:
            del config
            self.session = session

        async def run(self) -> None:
            async for _ in self.session.stream_reply("hello"):
                pass

    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: provider,
        ui_factory=FakeUI,
    )

    supplements = provider.agent_requests[0].supplements
    assert any("<project_instructions>" in item and "Python 3.12" in item for item in supplements)
    assert any("<project_memory>" in item and "project-stack.md" in item for item in supplements)
    assert all("Python 版本\ntype" not in item for item in supplements)


@pytest.mark.asyncio
async def test_anthropic_continue_restores_latest_session(tmp_path: Path) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path, "anthropic")
    manager = SessionManager(tmp_path, clock=lambda: datetime(2026, 8, 3, tzinfo=UTC))
    await manager.commit_turn(
        (
            TurnMessage(ChatMessage.user_text("old"), datetime(2026, 8, 3, tzinfo=UTC)),
            TurnMessage(ChatMessage.assistant_text("answer"), datetime(2026, 8, 3, tzinfo=UTC)),
        )
    )
    provider = FakeProvider([])
    histories = []

    class FakeUI:
        def __init__(self, config: object, session: object) -> None:
            del config
            self.session = session

        async def run(self) -> None:
            histories.append(self.session.history)

    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: provider,
        ui_factory=FakeUI,
        continue_session=True,
    )

    assert [message.text for message in histories[0]] == ["old", "answer"]


@pytest.mark.asyncio
async def test_openai_rejects_continue_before_provider_creation(tmp_path: Path) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path, "openai")
    calls = []

    with pytest.raises(ConfigError, match="仅支持 Anthropic"):
        await run_app(
            start_dir=tmp_path,
            provider_factory=lambda config: calls.append(config),  # type: ignore[arg-type,return-value]
            continue_session=True,
        )
    assert calls == []


@pytest.mark.asyncio
async def test_anthropic_loads_project_permission_mode_once(tmp_path: Path) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path, "anthropic")
    (tmp_path / ".ycode" / "security.yaml").write_text(
        "mode: allow\n",
        encoding="utf-8",
    )
    provider = FakeProvider([[TextDelta(0, "done"), StreamEnd(StopReason.END_TURN)]])
    seen_modes = []

    class FakeUI:
        def __init__(self, config: object, session: object) -> None:
            self._session = session
            seen_modes.append(session.permission_mode)

        async def run(self) -> None:
            async for _ in self._session.stream_reply("hello"):
                pass

    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: provider,
        ui_factory=FakeUI,
    )

    assert [mode.value for mode in seen_modes] == ["allow"]
    assert any("permission mode: allow" in item for item in provider.agent_requests[0].supplements)


@pytest.mark.asyncio
async def test_app_closes_provider_when_ui_fails(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)
    provider = FakeProvider([])

    class FailingUI:
        def __init__(self, config: object, session: object) -> None:
            return

        async def run(self) -> None:
            raise RuntimeError("ui failure")

    with pytest.raises(RuntimeError, match="ui failure"):
        await run_app(path, provider_factory=lambda config: provider, ui_factory=FailingUI)
    assert provider.closed is True


@pytest.mark.asyncio
async def test_anthropic_mcp_assembly_is_deferred_and_closes_manager_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path, "anthropic")
    with path.open("a", encoding="utf-8") as stream:
        stream.write("mcp_servers:\n  - name: demo\n    transport: stdio\n    command: python\n")
    close_order: list[str] = []

    class TrackingProvider(FakeProvider):
        async def close(self) -> None:
            close_order.append("provider")
            await super().close()

    provider = TrackingProvider([[TextDelta(0, "done"), StreamEnd(StopReason.END_TURN)]])

    class DeferredTool:
        definition = ToolDefinition(
            name="mcp_demo_echo",
            description="remote echo",
            access=ToolAccess.UNKNOWN,
            arguments=JsonSchemaToolArguments({"type": "object"}),
            defer_loading=True,
            timeout_error_code="mcp_timeout",
        )
        timeout_seconds = 1.0

        async def execute(self, arguments: object, context: ToolContext) -> ToolExecutionResult:
            del arguments, context
            return ToolExecutionResult("echo")

    class FakeManager:
        def __init__(self, config, registry, redactor) -> None:
            del config, redactor
            self.registry = registry
            self.warnings = ()
            self.callbacks = []

        def add_startup_callback(self, callback) -> None:
            self.callbacks.append(callback)

        def start_background(self) -> None:
            self.registry.register(DeferredTool())
            for callback in self.callbacks:
                callback()

        def set_security_warnings(self, warnings) -> None:
            self.warnings = warnings

        def snapshot(self) -> McpStatusReport:
            return McpStatusReport(
                (McpServerStatus("demo", "stdio", McpConnectionState.READY, 1),),
                self.warnings,
            )

        async def close(self) -> None:
            close_order.append("manager")

    monkeypatch.setattr("ycode.app.McpManager", FakeManager)
    seen_status = []

    class FakeUI:
        def __init__(self, config: object, session: object) -> None:
            del config
            self.session = session
            seen_status.append(session.mcp_status)

        async def run(self) -> None:
            async for _ in self.session.stream_reply("hello"):
                pass

    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: provider,
        ui_factory=FakeUI,
    )

    names = [definition.name for definition in provider.agent_requests[0].tools]
    assert "tool_search" in names
    assert "mcp_demo_echo" not in names
    assert any(
        "mcp_demo_echo" in supplement for supplement in provider.agent_requests[0].supplements
    )
    assert seen_status[0].ready_count == 1
    assert close_order == ["manager", "provider"]


@pytest.mark.asyncio
async def test_anthropic_runs_ui_before_background_mcp_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path, "anthropic")
    path.write_text(
        path.read_text(encoding="utf-8") + "mcp_servers:\n"
        "  - name: slow\n"
        "    transport: stdio\n"
        "    command: unused\n",
        encoding="utf-8",
    )
    events = []

    class BackgroundManager:
        def __init__(self, config, registry, redactor) -> None:
            del config, registry, redactor
            self.warnings = ()

        def set_security_warnings(self, warnings) -> None:
            self.warnings = warnings

        def add_startup_callback(self, callback) -> None:
            del callback

        def start_background(self) -> None:
            events.append("mcp_background")

        def snapshot(self) -> McpStatusReport:
            return McpStatusReport(
                (McpServerStatus("slow", "stdio", McpConnectionState.STARTING, 0),),
                self.warnings,
            )

        async def close(self) -> None:
            events.append("mcp_closed")

    monkeypatch.setattr("ycode.app.McpManager", BackgroundManager)

    class FakeUI:
        def __init__(self, config, session) -> None:
            del config
            self.session = session

        async def run(self) -> None:
            events.append("ui_running")
            assert self.session.mcp_status.starting_count == 1

    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: FakeProvider([]),
        ui_factory=FakeUI,
    )

    assert events == ["mcp_background", "ui_running", "mcp_closed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_mcp_unavailable_or_disabled_keeps_anthropic_agent_running(
    tmp_path: Path, enabled: bool
) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path, "anthropic")
    suffix = (
        "mcp_servers:\n"
        "  - name: offline\n"
        f"    enabled: {str(enabled).lower()}\n"
        "    transport: stdio\n"
        "    command: ycode-command-that-does-not-exist\n"
    )
    path.write_text(path.read_text(encoding="utf-8") + suffix, encoding="utf-8")
    provider = FakeProvider([[TextDelta(0, "builtins work"), StreamEnd(StopReason.END_TURN)]])
    reports = []

    class FakeUI:
        def __init__(self, config: object, session: object) -> None:
            del config
            self.session = session
            reports.append(session.mcp_status)

        async def run(self) -> None:
            async for _ in self.session.stream_reply("hello"):
                pass

    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: provider,
        ui_factory=FakeUI,
    )

    assert reports[0] is not None
    if enabled:
        assert reports[0].starting_count == 1
    else:
        assert reports[0].disabled_count == 1
    names = [item.name for item in provider.agent_requests[0].tools]
    for builtin in (
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
        "glob",
        "grep",
    ):
        assert builtin in names
    assert ("tool_search" in names) is enabled


@pytest.mark.asyncio
async def test_unavailable_mcp_security_reference_is_reported_as_warning(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path, "anthropic")
    path.write_text(
        path.read_text(encoding="utf-8") + "mcp_servers:\n"
        "  - name: offline\n"
        "    enabled: false\n"
        "    transport: stdio\n"
        "    command: unused\n",
        encoding="utf-8",
    )
    (tmp_path / ".ycode" / "security.yaml").write_text(
        "plan_only:\n  allow_mcp_tools:\n    - mcp_offline_echo\n",
        encoding="utf-8",
    )
    provider = FakeProvider([])
    reports = []

    class FakeUI:
        def __init__(self, config: object, session: object) -> None:
            del config
            reports.append(session.mcp_status)

        async def run(self) -> None:
            return

    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: provider,
        ui_factory=FakeUI,
    )

    assert [warning.code for warning in reports[0].security_warnings] == [
        "plan_only_mcp_tool_unavailable"
    ]
