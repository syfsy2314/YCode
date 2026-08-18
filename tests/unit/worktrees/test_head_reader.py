from datetime import UTC, datetime
from pathlib import Path

import pytest

from ycode.worktrees import (
    LinkedWorktreeHeadReader,
    WorktreeGitError,
    WorktreeLifecycle,
    WorktreeOwner,
    WorktreeRecord,
)


def make_linked_worktree(
    project: Path,
    *,
    oid: str,
    loose: bool = True,
) -> tuple[Path, WorktreeRecord]:
    common = project / ".git"
    private = common / "worktrees" / "writer"
    worktree = project / ".ycode" / "worktrees" / "agents" / "writer-a"
    private.mkdir(parents=True)
    worktree.mkdir(parents=True)
    pointer = worktree / ".git"
    pointer.write_text(f"gitdir: {private}\n", encoding="utf-8")
    (private / "gitdir").write_text(str(pointer), encoding="utf-8")
    (private / "commondir").write_text("../..\n", encoding="utf-8")
    (private / "HEAD").write_text("ref: refs/heads/ycode/agents--writer-a\n", encoding="utf-8")
    if loose:
        reference = common / "refs" / "heads" / "ycode" / "agents--writer-a"
        reference.parent.mkdir(parents=True)
        reference.write_text(f"{oid}\n", encoding="utf-8")
    else:
        (common / "packed-refs").write_text(
            f"# pack-refs with: peeled\n{oid} refs/heads/ycode/agents--writer-a\n",
            encoding="utf-8",
        )
    now = datetime.now(UTC)
    record = WorktreeRecord(
        1,
        "agents/writer-a",
        str(worktree),
        "ycode/agents--writer-a",
        "a" * 40,
        oid,
        WorktreeLifecycle.ACTIVE,
        now,
        now,
        True,
        (),
        WorktreeOwner("session", "task", 10, "instance"),
    )
    return worktree, record


@pytest.mark.parametrize("loose", [True, False])
def test_reader_resolves_head_without_git_process(tmp_path: Path, loose: bool) -> None:
    oid = "b" * 40
    worktree, record = make_linked_worktree(tmp_path, oid=oid, loose=loose)

    result = LinkedWorktreeHeadReader(tmp_path).read(worktree, record)

    assert result.branch == record.branch
    assert result.oid == oid
    assert result.common_git_dir == (tmp_path / ".git").resolve()


def test_reader_rejects_record_and_pointer_mismatch(tmp_path: Path) -> None:
    worktree, record = make_linked_worktree(tmp_path, oid="b" * 40)
    record = WorktreeRecord(
        record.version,
        record.name,
        record.path,
        "ycode/agents--other",
        record.base_head,
        record.current_head,
        record.lifecycle,
        record.created_at,
        record.last_activity_at,
        record.initialization_complete,
        record.initialization_warnings,
        record.owner,
    )

    with pytest.raises(WorktreeGitError, match="分支"):
        LinkedWorktreeHeadReader(tmp_path).read(worktree, record)


def test_reader_rejects_private_git_dir_outside_common_dir(tmp_path: Path) -> None:
    worktree, record = make_linked_worktree(tmp_path, oid="b" * 40)
    outside = tmp_path / "outside"
    outside.mkdir()
    (worktree / ".git").write_text(f"gitdir: {outside}\n", encoding="utf-8")

    with pytest.raises(WorktreeGitError, match="越界"):
        LinkedWorktreeHeadReader(tmp_path).read(worktree, record)
