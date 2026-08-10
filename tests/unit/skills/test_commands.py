from pathlib import Path

import pytest

from ycode.commands import CommandDispatcher, CommandRegistry
from ycode.skills.commands import build_skill_command_definitions
from ycode.skills.models import SkillConfig, SkillSnapshot


class Controller:
    def __init__(self) -> None:
        self.calls = []

    async def invoke_skill(self, name, arguments, raw_text):
        self.calls.append((name, arguments, raw_text))


def snapshot(name: str = "review") -> SkillSnapshot:
    root = Path("C:/workspace/.ycode/skills") / name
    return SkillSnapshot(
        name,
        "Review changes",
        root,
        root / "SKILL.md",
        "Review carefully.",
        SkillConfig(argument_hint="[focus]"),
        fingerprint="a" * 64,
    )


@pytest.mark.asyncio
async def test_dynamic_skill_command_preserves_raw_arguments_and_help_metadata() -> None:
    registry = CommandRegistry()
    registry.replace_dynamic(build_skill_command_definitions((snapshot(),)))
    controller = Controller()

    await CommandDispatcher(registry).try_dispatch("/review  parser   spaces ", controller)

    assert controller.calls == [("review", "parser   spaces", "/review  parser   spaces")]
    definition = registry.resolve("review")
    assert definition is not None
    assert definition.usage == "/review [focus]"
    assert definition.description == "Review changes"
