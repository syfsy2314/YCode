from pathlib import Path

import pytest

from ycode.security import PermissionMode
from ycode.subagents import SubagentRoleLoader, SubagentRoleValidationEnvironment


@pytest.fixture
def environment() -> SubagentRoleValidationEnvironment:
    return SubagentRoleValidationEnvironment(
        frozenset({"read_file", "grep", "write_file"}),
        frozenset({"reviewer"}),
    )


def write_role(path: Path, frontmatter: str, prompt: str = "Inspect code") -> None:
    path.write_text(f"---\n{frontmatter}\n---\n\n{prompt}\n", encoding="utf-8")


def test_loader_parses_full_role(
    tmp_path: Path,
    environment: SubagentRoleValidationEnvironment,
) -> None:
    path = tmp_path / "Review.md"
    write_role(
        path,
        "\n".join(
            (
                "name: review",
                "description: Review code",
                "model: reviewer",
                "allowed-tools: [read_file, grep]",
                "denied-tools: [write_file]",
                "max-rounds: 7",
                "permission: strict",
            )
        ),
    )

    entry = SubagentRoleLoader().load(path, environment)

    assert entry.available
    assert entry.role is not None
    assert entry.role.config.name == "review"
    assert entry.role.config.max_rounds == 7
    assert entry.role.config.permission is PermissionMode.STRICT
    assert entry.role.config.allowed_tools == frozenset({"read_file", "grep"})


@pytest.mark.parametrize(
    ("frontmatter", "prompt", "code"),
    [
        ("name: role\ndescription: ok\nunknown: true", "body", "frontmatter_field_unknown"),
        ("name: other\ndescription: ok", "body", "name_file_mismatch"),
        ("name: role\ndescription: ''", "body", "description_invalid"),
        ("name: role\ndescription: ok", "", "prompt_empty"),
        ("name: role\ndescription: ok\nmodel: missing", "body", "model_not_found"),
        (
            "name: role\ndescription: ok\nallowed-tools: [unknown]",
            "body",
            "tool_not_found",
        ),
        (
            "name: role\ndescription: ok\nallowed-tools: [grep]\ndenied-tools: [grep]",
            "body",
            "tool_lists_overlap",
        ),
        ("name: role\ndescription: ok\nmax-rounds: 0", "body", "max_rounds_invalid"),
        ("name: role\ndescription: ok\npermission: root", "body", "permission_invalid"),
    ],
)
def test_loader_isolates_invalid_roles(
    tmp_path: Path,
    environment: SubagentRoleValidationEnvironment,
    frontmatter: str,
    prompt: str,
    code: str,
) -> None:
    path = tmp_path / "role.md"
    write_role(path, frontmatter, prompt)

    entry = SubagentRoleLoader().load(path, environment)

    assert not entry.available
    assert code in {problem.code for problem in entry.problems}


def test_loader_rejects_invalid_frontmatter(
    tmp_path: Path,
    environment: SubagentRoleValidationEnvironment,
) -> None:
    path = tmp_path / "bad.md"
    path.write_text("name: bad", encoding="utf-8")

    entry = SubagentRoleLoader().load(path, environment)

    assert entry.problems[0].code == "frontmatter_missing"
