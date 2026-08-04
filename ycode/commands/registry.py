"""集中式命令注册中心。"""

import re
from dataclasses import replace

from ycode.commands.contracts import CommandCompletionEntry, CommandDefinition
from ycode.commands.errors import CommandConflictError, CommandDefinitionError

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


class CommandRegistry:
    def __init__(self) -> None:
        self._definitions: list[CommandDefinition] = []
        self._index: dict[str, CommandDefinition] = {}

    @property
    def definitions(self) -> tuple[CommandDefinition, ...]:
        return tuple(self._definitions)

    def register(self, definition: CommandDefinition) -> None:
        name = self._normalize_name(definition.name)
        aliases = tuple(self._normalize_name(alias) for alias in definition.aliases)
        if not definition.description.strip() or not definition.usage.strip():
            raise CommandDefinitionError("命令描述和用法不能为空")
        names = (name, *aliases)
        duplicate = next((item for item in names if names.count(item) > 1), None)
        if duplicate is not None:
            raise CommandConflictError(f"命令名称或别名冲突：/{duplicate}")
        conflict = next((item for item in names if item in self._index), None)
        if conflict is not None:
            raise CommandConflictError(f"命令名称或别名冲突：/{conflict}")

        normalized = replace(definition, name=name, aliases=aliases)
        self._definitions.append(normalized)
        for item in names:
            self._index[item] = normalized

    def resolve(self, name: str) -> CommandDefinition | None:
        return self._index.get(name.lower())

    def visible_definitions(self) -> tuple[CommandDefinition, ...]:
        return tuple(item for item in self._definitions if not item.hidden)

    def completion_entries(self) -> tuple[CommandCompletionEntry, ...]:
        entries = [
            CommandCompletionEntry(f"/{text}", definition.description, definition.name)
            for definition in self.visible_definitions()
            for text in (definition.name, *definition.aliases)
        ]
        return tuple(sorted(entries, key=lambda item: item.text))

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = name.lower()
        if not _NAME_PATTERN.fullmatch(normalized):
            raise CommandDefinitionError(f"无效命令名称：{name}")
        return normalized
