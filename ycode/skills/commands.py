"""生成项目 Skill 的动态 Slash Command。"""

from collections.abc import Sequence

from ycode.commands.contracts import (
    CommandDefinition,
    CommandInvocation,
    CommandKind,
    UIController,
)
from ycode.skills.models import SkillSnapshot


def build_skill_command_definitions(
    snapshots: Sequence[SkillSnapshot],
) -> tuple[CommandDefinition, ...]:
    definitions = []
    for snapshot in sorted(snapshots, key=lambda item: item.name.casefold()):

        async def handler(
            invocation: CommandInvocation,
            controller: UIController,
            *,
            skill_name: str = snapshot.name,
        ) -> None:
            arguments = invocation.arguments if invocation.arguments.strip() else None
            await controller.invoke_skill(skill_name, arguments, invocation.raw_text)

        hint = snapshot.config.argument_hint
        usage = f"/{snapshot.name}{f' {hint}' if hint else ''}"
        definitions.append(
            CommandDefinition(
                snapshot.name,
                (),
                snapshot.description,
                usage,
                CommandKind.AI,
                hint,
                handler,
            )
        )
    return tuple(definitions)


__all__ = ["build_skill_command_definitions"]
