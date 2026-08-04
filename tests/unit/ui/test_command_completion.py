from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from ycode.commands import CommandDefinition, CommandKind, build_command_runtime
from ycode.ui.command_completion import CommandCompleter


async def _hidden(invocation, controller) -> None:
    return None


def _texts(text: str, cursor_position: int | None = None) -> list[str]:
    hidden = CommandDefinition(
        "secret", (), "secret", "/secret", CommandKind.LOCAL, "", _hidden, True
    )
    completer = CommandCompleter(build_command_runtime((hidden,)).registry)
    document = Document(text, cursor_position=cursor_position)
    return [item.text for item in completer.get_completions(document, CompleteEvent())]


def test_unique_and_multiple_command_matches() -> None:
    assert _texts("/res") == ["/resume"]
    assert _texts("/p") == ["/permission", "/plan"]


def test_completion_only_applies_to_command_word_at_end() -> None:
    assert _texts("hello") == []
    assert _texts("/resume value") == []
    assert _texts("/secret") == []
    assert _texts("/resume", 3) == []
