import pytest

from ycode.commands import (
    CommandConflictError,
    CommandDefinition,
    CommandDefinitionError,
    CommandKind,
    CommandRegistry,
)


async def _handler(invocation, controller) -> None:
    return None


def _definition(name: str, aliases: tuple[str, ...] = (), *, hidden: bool = False):
    return CommandDefinition(
        name, aliases, "description", f"/{name}", CommandKind.LOCAL, "", _handler, hidden
    )


def test_registry_normalizes_resolves_and_sorts_completion() -> None:
    registry = CommandRegistry()
    registry.register(_definition("Zoo", ("Z",)))
    registry.register(_definition("Alpha", ("A",)))

    assert registry.resolve("ZOO").name == "zoo"
    assert [item.name for item in registry.visible_definitions()] == ["zoo", "alpha"]
    assert [item.text for item in registry.completion_entries()] == ["/a", "/alpha", "/z", "/zoo"]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_definition("help"), _definition("HELP")),
        (_definition("help", ("h",)), _definition("other", ("H",))),
        (_definition("help", ("other",)), _definition("OTHER")),
    ],
)
def test_registry_rejects_conflicts_atomically(first, second) -> None:
    registry = CommandRegistry()
    registry.register(first)
    before = registry.definitions
    with pytest.raises(CommandConflictError):
        registry.register(second)
    assert registry.definitions == before


def test_registry_rejects_internal_duplicates_and_invalid_names() -> None:
    registry = CommandRegistry()
    with pytest.raises(CommandConflictError):
        registry.register(_definition("help", ("HELP",)))
    with pytest.raises(CommandDefinitionError):
        registry.register(_definition("bad_name"))
    assert registry.definitions == ()


def test_hidden_command_resolves_but_is_not_visible() -> None:
    registry = CommandRegistry()
    registry.register(_definition("secret", ("s",), hidden=True))
    assert registry.resolve("s") is not None
    assert registry.visible_definitions() == ()
    assert registry.completion_entries() == ()
