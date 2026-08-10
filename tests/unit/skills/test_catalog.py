from pathlib import Path

import pytest

from ycode.skills import (
    SkillCatalog,
    SkillCatalogScanError,
    SkillLoader,
    SkillValidationEnvironment,
)


def test_repository_example_skills_load_with_expected_modes() -> None:
    root = Path(__file__).resolve().parents[3]
    environment = SkillValidationEnvironment(
        frozenset({"read_file", "grep", "run_command"}),
        frozenset(),
        frozenset({"help", "skills", "clear"}),
    )
    catalog = SkillCatalog(root, SkillLoader(), environment)
    state = catalog.scan_candidate()

    assert set(state.available) == {"commit", "review", "test"}
    assert state.available["commit"].config.execution_mode.value == "shared"
    assert state.available["review"].config.context_kind.value == "recent"
    assert state.available["review"].config.recent_turns == 5
    assert state.available["test"].config.context_kind.value == "none"


ENVIRONMENT = SkillValidationEnvironment(
    frozenset({"read_file", "grep"}),
    frozenset({"main"}),
    frozenset({"help", "clear"}),
)


def _catalog(root: Path) -> SkillCatalog:
    return SkillCatalog(root, SkillLoader(), ENVIRONMENT)


def _write(root: Path, folder: str, *, name: str | None = None, body: str = "SOP") -> Path:
    skill_root = root / ".ycode" / "skills" / folder
    skill_root.mkdir(parents=True)
    path = skill_root / "SKILL.md"
    path.write_text(
        f"---\nname: {name or folder}\ndescription: {folder} skill\n---\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_missing_root_produces_empty_catalog(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)

    candidate = catalog.scan_candidate()

    assert candidate.entries == ()


def test_scans_only_direct_skill_directories_in_stable_order(tmp_path: Path) -> None:
    _write(tmp_path, "zeta")
    _write(tmp_path, "alpha")
    loose = tmp_path / ".ycode" / "skills" / "loose.md"
    loose.write_text("ignored", encoding="utf-8")
    nested = tmp_path / ".ycode" / "skills" / "group" / "nested"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("ignored", encoding="utf-8")

    candidate = _catalog(tmp_path).scan_candidate()

    assert [entry.directory_name for entry in candidate.entries] == ["alpha", "group", "zeta"]
    assert not candidate.entries[1].available
    assert candidate.entries[1].problems[0].code == "skill_file_missing"


def test_invalid_skill_does_not_hide_valid_skill(tmp_path: Path) -> None:
    _write(tmp_path, "valid")
    broken = _write(tmp_path, "broken")
    broken.write_text("not frontmatter", encoding="utf-8")

    candidate = _catalog(tmp_path).scan_candidate()

    assert set(candidate.available) == {"valid"}
    assert any(
        entry.directory_name == "broken" and not entry.available for entry in candidate.entries
    )


def test_builtin_command_conflict_is_unavailable(tmp_path: Path) -> None:
    _write(tmp_path, "help")

    candidate = _catalog(tmp_path).scan_candidate()

    assert not candidate.entries[0].available
    assert candidate.entries[0].problems[-1].code == "builtin_command_conflict"


def test_candidate_is_not_visible_until_commit(tmp_path: Path) -> None:
    _write(tmp_path, "review")
    catalog = _catalog(tmp_path)

    candidate = catalog.scan_candidate()

    assert catalog.entries == ()
    catalog.commit(candidate)
    assert catalog.get_available("review") is not None


def test_reload_one_reads_existing_path_but_does_not_discover_new_directory(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, "review", body="Old SOP")
    catalog = _catalog(tmp_path)
    catalog.reload()
    path.write_text(
        "---\nname: review\ndescription: review skill\n---\nNew SOP\n",
        encoding="utf-8",
    )
    _write(tmp_path, "new-skill")

    refreshed = catalog.reload_one("review")

    assert refreshed.snapshot is not None
    assert refreshed.snapshot.instructions == "New SOP"
    assert catalog.get_entry("new-skill") is None
    assert catalog.get_available("review").instructions == "Old SOP"  # type: ignore[union-attr]


def test_reload_one_failure_keeps_committed_snapshot(tmp_path: Path) -> None:
    path = _write(tmp_path, "review", body="Old SOP")
    catalog = _catalog(tmp_path)
    catalog.reload()
    path.write_text("broken", encoding="utf-8")

    refreshed = catalog.reload_one("review")

    assert not refreshed.available
    assert catalog.get_available("review").instructions == "Old SOP"  # type: ignore[union-attr]


def test_scan_failure_does_not_replace_old_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "review")
    catalog = _catalog(tmp_path)
    catalog.reload()

    def fail_iterdir(self: Path):
        raise OSError("denied")

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    with pytest.raises(SkillCatalogScanError):
        catalog.reload()
    assert catalog.get_available("review") is not None


def test_catalog_text_contains_only_available_name_and_description(tmp_path: Path) -> None:
    _write(tmp_path, "review", body="SECRET SOP")
    broken = _write(tmp_path, "broken")
    broken.write_text("broken", encoding="utf-8")
    catalog = _catalog(tmp_path)
    catalog.reload()

    text = catalog.catalog_text()

    assert "review: review skill" in text
    assert "SECRET SOP" not in text
    assert "broken" not in text
