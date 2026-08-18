from pathlib import Path

import pytest

from ycode.worktrees import (
    WorktreeGitError,
    parse_commit_records,
    parse_status_porcelain_v2,
    parse_worktree_porcelain,
)


def test_parse_worktree_porcelain_z() -> None:
    oid = "a" * 40
    data = (
        f"worktree C:/repo\0HEAD {oid}\0branch refs/heads/main\0\0"
        f"worktree C:/repo/wt\0HEAD {oid}\0branch refs/heads/ycode/agents--writer\0"
        "locked session/task\0\0"
    ).encode()

    entries = parse_worktree_porcelain(data)

    assert entries[0].path == Path("C:/repo").resolve(strict=False)
    assert entries[0].branch == "main"
    assert entries[1].branch == "ycode/agents--writer"
    assert entries[1].locked


def test_parse_status_porcelain_v2_classifies_changes() -> None:
    oid = "b" * 40
    data = (
        f"# branch.oid {oid}\0# branch.head ycode/agents--writer\0"
        "# branch.upstream origin/writer\0# branch.ab +1 -0\0"
        "1 M. N... 100644 100644 100644 aaaaaaa bbbbbbb staged.py\0"
        "1 .M N... 100644 100644 100644 aaaaaaa bbbbbbb modified.py\0"
        "? new.py\0"
    ).encode()

    status = parse_status_porcelain_v2(data)

    assert status.head == oid
    assert status.branch == "ycode/agents--writer"
    assert status.upstream == "origin/writer"
    assert status.staged == ("staged.py",)
    assert status.modified == ("modified.py",)
    assert status.untracked == ("new.py",)


def test_parse_commit_records_rejects_incomplete_output() -> None:
    commits = parse_commit_records(f"{'a' * 40}\0first\0\n{'b' * 40}\0second\0".encode())
    assert [item.subject for item in commits] == ["first", "second"]

    with pytest.raises(WorktreeGitError, match="commit"):
        parse_commit_records(f"{'a' * 40}\0".encode())
