import pytest

from ycode.commands import CommandParser


@pytest.mark.parametrize("text", ["hello", "  hello", ""])
def test_non_command_returns_none(text: str) -> None:
    assert CommandParser().parse(text) is None


@pytest.mark.parametrize(
    ("text", "name", "arguments"),
    [
        (" /PLAN ", "plan", ""),
        ("/resume Session-AbC", "resume", "Session-AbC"),
        ("/resume\tSession-AbC  x ", "resume", "Session-AbC  x"),
        ("/", "", ""),
        ("/ help", "", "help"),
        ("//value", "/value", ""),
    ],
)
def test_parser_preserves_argument_text(text: str, name: str, arguments: str) -> None:
    invocation = CommandParser().parse(text)
    assert invocation is not None
    assert (invocation.name, invocation.arguments) == (name, arguments)
