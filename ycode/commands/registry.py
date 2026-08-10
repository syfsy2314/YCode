"""集中式命令注册中心。"""

import re
from dataclasses import replace

from ycode.commands.contracts import CommandCompletionEntry, CommandDefinition
from ycode.commands.errors import CommandConflictError, CommandDefinitionError

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


class CommandRegistry:
    def __init__(self) -> None:
        self._static_definitions: list[CommandDefinition] = []
        self._dynamic_definitions: list[CommandDefinition] = []
        self._index: dict[str, CommandDefinition] = {}

    @property
    def definitions(self) -> tuple[CommandDefinition, ...]:
        return (*self._static_definitions, *self._dynamic_definitions)

    def register(self, definition: CommandDefinition) -> None:
        normalized, names = self._normalize_definition(definition)
        self._ensure_no_conflict(names, self._index)
        self._static_definitions.append(normalized)
        self._index.update({item: normalized for item in names})

    def replace_dynamic(self, definitions: tuple[CommandDefinition, ...]) -> None:
        candidate_index = {
            name: definition
            for definition in self._static_definitions
            for name in (definition.name, *definition.aliases)
        }
        normalized_items: list[CommandDefinition] = []
        for definition in sorted(definitions, key=lambda item: item.name.casefold()):
            normalized, names = self._normalize_definition(definition)
            self._ensure_no_conflict(names, candidate_index)
            normalized_items.append(normalized)
            candidate_index.update({item: normalized for item in names})
        self._dynamic_definitions[:] = normalized_items
        self._index = candidate_index

    def resolve(self, name: str) -> CommandDefinition | None:
        return self._index.get(name.lower())

    def visible_definitions(self) -> tuple[CommandDefinition, ...]:
        return tuple(item for item in self.definitions if not item.hidden)

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

    def _normalize_definition(
        self, definition: CommandDefinition
    ) -> tuple[CommandDefinition, tuple[str, ...]]:
        name = self._normalize_name(definition.name)
        aliases = tuple(self._normalize_name(alias) for alias in definition.aliases)
        if not definition.description.strip() or not definition.usage.strip():
            raise CommandDefinitionError("命令描述和用法不能为空")
        names = (name, *aliases)
        duplicate = next((item for item in names if names.count(item) > 1), None)
        if duplicate is not None:
            raise CommandConflictError(f"命令名称或别名冲突：/{duplicate}")
        return replace(definition, name=name, aliases=aliases), names

    @staticmethod
    def _ensure_no_conflict(names: tuple[str, ...], index: dict[str, CommandDefinition]) -> None:
        conflict = next((item for item in names if item in index), None)
        if conflict is not None:
            raise CommandConflictError(f"命令名称或别名冲突：/{conflict}")
