"""prompt_toolkit 命令名称补全适配。"""

from collections.abc import Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from ycode.commands import CommandRegistry


class CommandCompleter(Completer):
    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    def get_completions(
        self,
        document: Document,
        complete_event,
    ) -> Iterable[Completion]:
        del complete_event
        if document.cursor_position != len(document.text):
            return
        prefix = document.text_before_cursor
        if not prefix.startswith("/") or any(character.isspace() for character in prefix):
            return
        lowered = prefix.lower()
        for entry in self._registry.completion_entries():
            if entry.text.startswith(lowered):
                yield Completion(
                    entry.text,
                    start_position=-len(prefix),
                    display_meta=entry.description,
                )
