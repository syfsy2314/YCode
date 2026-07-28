from pathlib import Path

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.app import run_app
from ycode.config.models import ProviderConfig
from ycode.core import StopReason, StreamEnd, TextDelta


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


@pytest.mark.asyncio
async def test_app_assembles_anthropic_agent_with_builtin_tools(tmp_path: Path) -> None:
    path = tmp_path / ".ycode" / "config.yaml"
    write_config(path, "anthropic")
    provider = FakeProvider([[TextDelta(0, "done"), StreamEnd(StopReason.END_TURN)]])

    class FakeUI:
        def __init__(self, config: object, session: object) -> None:
            self._session = session

        async def run(self) -> None:
            async for _ in self._session.stream_reply("hello"):
                pass

    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: provider,
        ui_factory=FakeUI,
    )

    assert [definition.name for definition in provider.tool_definitions[0]] == [
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
        "glob",
        "grep",
    ]
    assert f"Workspace: {tmp_path}" in provider.system_prompts[0]
    assert provider.closed is True


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
