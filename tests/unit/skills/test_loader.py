from pathlib import Path

from ycode.skills import (
    SkillContextKind,
    SkillExecutionMode,
    SkillProblemSeverity,
    SkillValidationEnvironment,
)
from ycode.skills.loader import SkillLoader

TOOLS = frozenset(
    {"read_file", "write_file", "edit_file", "run_command", "glob", "grep", "tool_search"}
)
ENVIRONMENT = SkillValidationEnvironment(
    TOOLS, frozenset({"main", "reviewer"}), frozenset({"help"})
)


def _write_skill(root: Path, name: str, frontmatter: str, body: str = "Follow the SOP.") -> Path:
    skill_root = root / name
    skill_root.mkdir(parents=True)
    path = skill_root / "SKILL.md"
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")
    return path


def test_loads_minimal_standard_skill_with_defaults(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, "review", "name: review\ndescription: Review changes")

    entry = SkillLoader().load(path, ENVIRONMENT)

    assert entry.available
    assert entry.snapshot is not None
    assert entry.snapshot.config.execution_mode is SkillExecutionMode.SHARED
    assert entry.snapshot.config.context_kind is SkillContextKind.CURRENT
    assert entry.snapshot.config.visible_tools is None
    assert entry.snapshot.config.allowed_tools == frozenset()
    assert len(entry.snapshot.fingerprint) == 64


def test_allows_empty_standard_body(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, "empty", "name: empty\ndescription: Empty procedure", "")

    entry = SkillLoader().load(path, ENVIRONMENT)

    assert entry.available
    assert entry.snapshot is not None
    assert entry.snapshot.instructions == ""


def test_rejects_invalid_frontmatter_and_directory_mismatch(tmp_path: Path) -> None:
    invalid = _write_skill(tmp_path, "broken", "name: [broken\ndescription: Broken")
    mismatch = _write_skill(tmp_path, "folder", "name: other\ndescription: Wrong directory")

    invalid_entry = SkillLoader().load(invalid, ENVIRONMENT)
    mismatch_entry = SkillLoader().load(mismatch, ENVIRONMENT)

    assert not invalid_entry.available
    assert invalid_entry.problems[0].code == "frontmatter_invalid"
    assert not mismatch_entry.available
    assert any(problem.code == "name_directory_mismatch" for problem in mismatch_entry.problems)


def test_parses_isolated_recent_extensions(tmp_path: Path) -> None:
    path = _write_skill(
        tmp_path,
        "review",
        """name: review
description: Review changes
metadata:
  ycode-execution-mode: isolated
  ycode-model: reviewer
  ycode-context: recent
  ycode-recent-turns: "5"
  ycode-visible-tools: Read Grep
  ycode-argument-hint: <scope>""",
    )

    entry = SkillLoader().load(path, ENVIRONMENT)

    assert entry.available
    assert entry.snapshot is not None
    config = entry.snapshot.config
    assert config.execution_mode is SkillExecutionMode.ISOLATED
    assert config.context_kind is SkillContextKind.RECENT
    assert config.recent_turns == 5
    assert config.model_name == "reviewer"
    assert config.visible_tools == frozenset({"read_file", "grep"})
    assert config.argument_hint == "<scope>"


def test_rejects_invalid_execution_combinations(tmp_path: Path) -> None:
    path = _write_skill(
        tmp_path,
        "bad",
        """name: bad
description: Invalid shared model
metadata:
  ycode-model: reviewer""",
    )

    entry = SkillLoader().load(path, ENVIRONMENT)

    assert not entry.available
    assert any(problem.code == "execution_config_invalid" for problem in entry.problems)


def test_maps_plain_tools_and_safely_downgrades_parameter_expression(tmp_path: Path) -> None:
    path = _write_skill(
        tmp_path,
        "git-review",
        """name: git-review
description: Review Git changes
allowed-tools: Read Bash(git:*)
metadata:
  ycode-visible-tools: Read Bash""",
    )

    entry = SkillLoader().load(path, ENVIRONMENT)

    assert entry.available
    assert entry.snapshot is not None
    assert entry.snapshot.config.visible_tools == frozenset({"read_file", "run_command"})
    assert entry.snapshot.config.allowed_tools == frozenset({"read_file"})
    assert any(
        problem.code == "tool_expression_unsupported"
        and problem.severity is SkillProblemSeverity.WARNING
        for problem in entry.problems
    )


def test_unknown_plain_tool_makes_skill_unavailable(tmp_path: Path) -> None:
    path = _write_skill(
        tmp_path,
        "unknown-tool",
        "name: unknown-tool\ndescription: Uses missing tool\nallowed-tools: MissingTool",
    )

    entry = SkillLoader().load(path, ENVIRONMENT)

    assert not entry.available
    assert any(problem.code == "tool_not_found" for problem in entry.problems)


def test_builtin_command_name_is_unavailable(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, "help", "name: help\ndescription: Conflicts with help")

    entry = SkillLoader().load(path, ENVIRONMENT)

    assert not entry.available
    assert any(problem.code == "builtin_command_conflict" for problem in entry.problems)
