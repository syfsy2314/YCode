import pytest

from ycode.prompt import (
    PromptBundle,
    PromptSection,
    SupplementKind,
    SupplementScope,
    SystemSupplement,
)


def test_prompt_bundle_sorts_sections_and_exposes_stable_views() -> None:
    bundle = PromptBundle(
        (
            PromptSection("tools", 20, "Tool rules."),
            PromptSection("behavior", 10, "Behavior rules."),
            PromptSection("identity", 10, "Identity rules."),
        )
    )

    assert bundle.section_ids == ("behavior", "identity", "tools")
    assert bundle.content_blocks == (
        "Behavior rules.",
        "Identity rules.",
        "Tool rules.",
    )
    assert bundle.text == "Behavior rules.\n\nIdentity rules.\n\nTool rules."


@pytest.mark.parametrize(
    ("section_id", "priority", "content"),
    [
        ("Invalid", 1, "content"),
        ("invalid_id", 1, "content"),
        ("valid", -1, "content"),
        ("valid", True, "content"),
        ("valid", 1, "   "),
    ],
)
def test_prompt_section_rejects_invalid_fields(
    section_id: str,
    priority: object,
    content: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        PromptSection(section_id, priority, content)  # type: ignore[arg-type]


def test_prompt_bundle_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="ID 重复"):
        PromptBundle(
            (
                PromptSection("identity", 10, "first"),
                PromptSection("identity", 20, "second"),
            )
        )


def test_system_supplement_renders_fixed_tag_and_scope() -> None:
    supplement = SystemSupplement(
        SupplementKind.ENVIRONMENT,
        "Workspace: D:\\project",
        SupplementScope.SESSION,
    )

    assert supplement.tagged_content == (
        "<environment_context>\nWorkspace: D:\\project\n</environment_context>"
    )
    assert supplement.scope is SupplementScope.SESSION


@pytest.mark.parametrize(
    ("kind", "tag"),
    [
        (SupplementKind.PROJECT_INSTRUCTIONS, "project_instructions"),
        (SupplementKind.PROJECT_MEMORY, "project_memory"),
    ],
)
def test_project_supplement_types_have_distinct_tags(
    kind: SupplementKind,
    tag: str,
) -> None:
    assert SystemSupplement(kind, "content").tagged_content.startswith(f"<{tag}>\n")


def test_system_supplement_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="内容不能为空"):
        SystemSupplement(SupplementKind.MODE, "")
    with pytest.raises(TypeError, match="类型"):
        SystemSupplement("mode", "content")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="生命周期"):
        SystemSupplement(
            SupplementKind.MODE,
            "content",
            "session",  # type: ignore[arg-type]
        )
