import pytest

from ycode.prompt import PromptBuilder, PromptResource, build_builtin_prompt


def test_builtin_prompt_loads_all_sections_in_stable_order() -> None:
    first = build_builtin_prompt()
    second = build_builtin_prompt()

    assert first.section_ids == (
        "identity",
        "behavior",
        "tool-use",
        "coding",
        "safety",
        "output",
    )
    assert first == second
    assert first.text == second.text
    assert "terminal coding assistant" in first.text
    assert "dedicated tool" in first.text
    assert "Read a file before editing it" in first.text


def test_prompt_builder_sorts_custom_resources_deterministically() -> None:
    values = {"later.md": "later", "earlier.md": "earlier"}
    builder = PromptBuilder(
        (
            PromptResource("later", 20, "later.md"),
            PromptResource("earlier", 10, "earlier.md"),
        ),
        loader=values.__getitem__,
    )

    bundle = builder.build()

    assert bundle.section_ids == ("earlier", "later")
    assert bundle.text == "earlier\n\nlater"


def test_prompt_builder_rejects_duplicate_ids() -> None:
    builder = PromptBuilder(
        (
            PromptResource("same", 10, "one.md"),
            PromptResource("same", 20, "two.md"),
        ),
        loader=lambda filename: filename,
    )

    with pytest.raises(ValueError, match="ID 重复"):
        builder.build()


def test_prompt_builder_reports_missing_resource() -> None:
    def missing(filename: str) -> str:
        raise FileNotFoundError(filename)

    builder = PromptBuilder(
        (PromptResource("missing", 10, "missing.md"),),
        loader=missing,
    )

    with pytest.raises(RuntimeError, match="missing.md"):
        builder.build()


def test_prompt_builder_rejects_empty_resource() -> None:
    builder = PromptBuilder(
        (PromptResource("empty", 10, "empty.md"),),
        loader=lambda filename: " \n",
    )

    with pytest.raises(ValueError, match="正文不能为空"):
        builder.build()
