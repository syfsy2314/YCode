from pathlib import Path

from ycode.subagents import (
    SubagentRoleCatalog,
    SubagentRoleLoader,
    SubagentRoleValidationEnvironment,
)


def environment() -> SubagentRoleValidationEnvironment:
    return SubagentRoleValidationEnvironment(
        frozenset({"read_file", "glob", "grep", "tool_search"}),
        frozenset(),
    )


def write_role(path: Path, name: str) -> None:
    path.write_text(
        f"---\nname: {name}\ndescription: custom role\n---\n\nDo work.\n",
        encoding="utf-8",
    )


def test_catalog_always_loads_read_only_builtins(tmp_path: Path) -> None:
    catalog = SubagentRoleCatalog(tmp_path, SubagentRoleLoader(), environment())

    catalog.load()

    explore = catalog.get_available("EXPLORE")
    plan = catalog.get_available("plan")
    assert explore is not None and explore.builtin
    assert plan is not None and plan.builtin
    assert explore.config.allowed_tools == frozenset({"read_file", "glob", "grep"})


def test_catalog_scans_only_direct_markdown_and_isolates_builtin_conflict(tmp_path: Path) -> None:
    root = tmp_path / ".ycode" / "agents"
    root.mkdir(parents=True)
    write_role(root / "explore.md", "explore")
    write_role(root / "review.md", "review")
    write_role(root / "valid.md", "valid")
    nested = root / "nested"
    nested.mkdir()
    write_role(nested / "ignored.md", "ignored")
    (root / "ignored.txt").write_text("ignored", encoding="utf-8")
    catalog = SubagentRoleCatalog(tmp_path, SubagentRoleLoader(), environment())

    entries = catalog.load()

    assert catalog.get_available("valid") is not None
    assert catalog.get_available("review") is not None
    assert catalog.get_available("ignored") is None
    assert catalog.get_available("explore") is not None
    codes = {problem.code for entry in entries for problem in entry.problems}
    assert "builtin_role_conflict" in codes


def test_catalog_marks_all_normalized_project_conflicts(tmp_path: Path) -> None:
    loader = SubagentRoleLoader()
    first = loader.load_text(
        "---\nname: review\ndescription: first\n---\nbody",
        source="first",
        file_stem="Review",
        environment=environment(),
    )
    second = loader.load_text(
        "---\nname: REVIEW\ndescription: second\n---\nbody",
        source="second",
        file_stem="review",
        environment=environment(),
    )

    entries = SubagentRoleCatalog._mark_conflicts([first, second], set())

    assert all(entry.role is None for entry in entries)
    assert all(entry.problems[-1].code == "role_name_conflict" for entry in entries)


def test_invalid_project_role_does_not_hide_valid_role(tmp_path: Path) -> None:
    root = tmp_path / ".ycode" / "agents"
    root.mkdir(parents=True)
    (root / "bad.md").write_text("invalid", encoding="utf-8")
    write_role(root / "good.md", "good")
    catalog = SubagentRoleCatalog(tmp_path, SubagentRoleLoader(), environment())

    catalog.load()

    assert catalog.get_available("good") is not None
    assert catalog.get_available("bad") is None
