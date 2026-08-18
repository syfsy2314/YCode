"""受管 Worktree 名称与临时分支映射。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_SEGMENT_PATTERN = re.compile(r"^[a-z0-9._-]+$")
_ROLE_REPLACEMENT = re.compile(r"[^a-z0-9._-]+")
_RESERVED_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_MAX_SEGMENTS = 4
_MAX_SEGMENT_LENGTH = 32
_MAX_NAME_LENGTH = 96
_BRANCH_PREFIX = "ycode/"


@dataclass(frozen=True, slots=True)
class WorktreeName:
    value: str

    def __post_init__(self) -> None:
        _validate_name(self.value)

    @classmethod
    def parse(cls, value: str) -> WorktreeName:
        if not isinstance(value, str):
            raise TypeError("Worktree 名称必须是字符串")
        return cls(value)

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(self.value.split("/"))

    @property
    def flat_name(self) -> str:
        return "--".join(self.segments)

    @property
    def branch(self) -> str:
        return f"{_BRANCH_PREFIX}{self.flat_name}"


def managed_worktree_name(role: str, task_id: str, *, attempt: int = 0) -> WorktreeName:
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
        raise ValueError("Worktree 名称重试序号必须是非负整数")
    role_slug = _ROLE_REPLACEMENT.sub("-", role.strip().lower())
    role_slug = re.sub(r"-+", "-", role_slug).strip("-.") or "agent"
    digest = hashlib.sha256(f"{task_id}:{attempt}".encode()).hexdigest()[:10]
    maximum_role = _MAX_SEGMENT_LENGTH - len(digest) - 1
    role_slug = role_slug[:maximum_role].rstrip("-.") or "agent"
    return WorktreeName(f"agents/{role_slug}-{digest}")


def worktree_name_from_branch(branch: str) -> WorktreeName:
    if not isinstance(branch, str) or not branch.startswith(_BRANCH_PREFIX):
        raise ValueError("临时分支必须位于 ycode/ 命名空间")
    flat_name = branch.removeprefix(_BRANCH_PREFIX)
    if not flat_name:
        raise ValueError("临时分支缺少 Worktree 名称")
    return WorktreeName(flat_name.replace("--", "/"))


def _validate_name(value: str) -> None:
    if not value or len(value) > _MAX_NAME_LENGTH or "\\" in value:
        raise ValueError("Worktree 名称为空、过长或包含反斜杠")
    segments = value.split("/")
    if len(segments) > _MAX_SEGMENTS:
        raise ValueError("Worktree 名称分段过多")
    for segment in segments:
        if not segment or segment in {".", ".."}:
            raise ValueError("Worktree 名称包含空段、. 或 ..")
        if len(segment) > _MAX_SEGMENT_LENGTH:
            raise ValueError("Worktree 名称单段过长")
        if not _SEGMENT_PATTERN.fullmatch(segment):
            raise ValueError("Worktree 名称包含非法字符")
        if segment.endswith(".") or "--" in segment:
            raise ValueError("Worktree 名称包含段尾点或连续 --")
        if segment.split(".", 1)[0] in _RESERVED_NAMES:
            raise ValueError("Worktree 名称包含 Windows 保留名称")
