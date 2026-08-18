"""Git Worktree 本地操作与纯文件系统身份读取。"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ycode.worktrees.models import (
    WorktreeCommit,
    WorktreeDeleteDecision,
    WorktreeLifecycle,
    WorktreeRecord,
    WorktreeStatusSnapshot,
)

_OID_PATTERN = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


def _resolved_path(value: str | Path, *, strict: bool) -> Path:
    return Path(value).resolve(strict=strict)


class WorktreeGitError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LinkedWorktreeHead:
    branch: str | None
    oid: str
    private_git_dir: Path
    common_git_dir: Path


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass(frozen=True, slots=True)
class GitWorktreeEntry:
    path: Path
    head: str | None
    branch: str | None
    locked: bool


GitRunner = Callable[[Path, tuple[str, ...], Mapping[str, str]], Awaitable[GitCommandResult]]


async def _run_git(
    cwd: Path,
    arguments: tuple[str, ...],
    environment: Mapping[str, str],
) -> GitCommandResult:
    process_environment = os.environ.copy()
    process_environment.update(environment)
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(cwd),
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=process_environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    stdout, stderr = await process.communicate()
    return GitCommandResult(process.returncode, stdout, stderr)


class GitWorktreeClient:
    def __init__(
        self,
        project_root: str | Path,
        *,
        environment: Mapping[str, str] | None = None,
        runner: GitRunner = _run_git,
    ) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        self._environment = dict(environment or {})
        self._runner = runner

    def with_environment(self, environment: Mapping[str, str]) -> GitWorktreeClient:
        return GitWorktreeClient(
            self.project_root,
            environment={**self._environment, **environment},
            runner=self._runner,
        )

    async def ensure_repository(self) -> str:
        result = await self._command(("rev-parse", "--show-toplevel"), check=False)
        if result.exit_code != 0:
            raise WorktreeGitError("git_unavailable", "当前项目不是可用的 Git 仓库。")
        try:
            top = _resolved_path(result.stdout.decode().strip(), strict=True)
        except (OSError, UnicodeError) as error:
            raise WorktreeGitError("git_unavailable", "Git 仓库根目录无法确认。") from error
        if os.path.normcase(str(top)) != os.path.normcase(str(self.project_root)):
            raise WorktreeGitError("git_root_mismatch", "Git 仓库根目录与项目根目录不一致。")
        head = await self._command(("rev-parse", "--verify", "HEAD"), check=False)
        if head.exit_code != 0:
            raise WorktreeGitError("no_initial_commit", "Git 仓库还没有初始 commit。")
        return _decode_oid(head.stdout)

    async def head(self, worktree: str | Path) -> str:
        result = await self._command(("rev-parse", "--verify", "HEAD"), cwd=worktree)
        return _decode_oid(result.stdout)

    async def branch_exists(self, branch: str) -> bool:
        result = await self._command(
            ("show-ref", "--verify", "--quiet", f"refs/heads/{branch}"),
            check=False,
        )
        if result.exit_code == 0:
            return True
        if result.exit_code == 1:
            return False
        raise WorktreeGitError("git_check_failed", "无法确认临时分支是否存在。")

    async def create(
        self,
        path: str | Path,
        branch: str,
        base: str,
        reason: str,
    ) -> None:
        await self._command(
            (
                "worktree",
                "add",
                "--lock",
                "--reason",
                reason,
                "-b",
                branch,
                str(_resolved_path(path, strict=False)),
                base,
            )
        )

    async def list_worktrees(self) -> tuple[GitWorktreeEntry, ...]:
        result = await self._command(("worktree", "list", "--porcelain", "-z"))
        return parse_worktree_porcelain(result.stdout)

    async def unlock(self, path: str | Path) -> None:
        result = await self._command(
            ("worktree", "unlock", str(_resolved_path(path, strict=False))),
            check=False,
        )
        if result.exit_code not in {0, 128}:
            self._raise_command_error(result)

    async def remove(self, path: str | Path, *, force: bool = False) -> None:
        arguments = ["worktree", "remove"]
        if force:
            arguments.append("--force")
        arguments.append(str(_resolved_path(path, strict=False)))
        await self._command(tuple(arguments))

    async def delete_branch(self, branch: str) -> None:
        await self._command(("branch", "-D", "--", branch))

    async def config_path(self, cwd: str | Path, key: str) -> str | None:
        result = await self._command(("config", "--path", "--get", key), cwd=cwd, check=False)
        if result.exit_code == 1:
            return None
        if result.exit_code != 0:
            self._raise_command_error(result)
        return result.stdout.decode("utf-8", errors="strict").strip()

    async def git_path(self, cwd: str | Path, name: str) -> Path:
        result = await self._command(("rev-parse", "--git-path", name), cwd=cwd)
        value = result.stdout.decode("utf-8", errors="strict").strip()
        path = Path(value)
        return _resolved_path(path if path.is_absolute() else Path(cwd) / path, strict=False)

    async def is_ignored(self, relative_path: str) -> bool:
        result = await self._command(
            ("check-ignore", "--quiet", "--", relative_path),
            check=False,
        )
        if result.exit_code == 0:
            return True
        if result.exit_code == 1:
            return False
        self._raise_command_error(result)

    async def status(self, worktree: str | Path, base: str) -> WorktreeStatusSnapshot:
        path = _resolved_path(worktree, strict=True)
        status_result = await self._command(
            ("status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"),
            cwd=path,
        )
        parsed = parse_status_porcelain_v2(status_result.stdout)
        head = parsed.head or await self.head(path)
        commits = await self._commits(path, base, "HEAD")
        diff_stat = (
            (await self._command(("diff", "--stat", "--no-ext-diff", base), cwd=path))
            .stdout.decode("utf-8", errors="replace")
            .strip()
        )
        unpushed: tuple[WorktreeCommit, ...] = ()
        if commits and parsed.upstream is not None:
            unpushed = await self._commits(path, base, "HEAD", not_revision=parsed.upstream)
        return WorktreeStatusSnapshot(
            head,
            parsed.staged,
            parsed.modified,
            parsed.untracked,
            commits,
            diff_stat,
            parsed.upstream,
            unpushed,
            datetime.now(UTC),
        )

    async def _commits(
        self,
        cwd: Path,
        base: str,
        head: str,
        *,
        not_revision: str | None = None,
    ) -> tuple[WorktreeCommit, ...]:
        arguments = ["log", "--format=%H%x00%s%x00", f"{base}..{head}"]
        if not_revision is not None:
            arguments.extend(("--not", not_revision))
        result = await self._command(tuple(arguments), cwd=cwd)
        return parse_commit_records(result.stdout)

    async def _command(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: str | Path | None = None,
        check: bool = True,
    ) -> GitCommandResult:
        try:
            result = await self._runner(
                _resolved_path(cwd, strict=True) if cwd is not None else self.project_root,
                arguments,
                self._environment,
            )
        except (OSError, RuntimeError) as error:
            raise WorktreeGitError("git_unavailable", "Git 子进程无法启动。") from error
        if check and result.exit_code != 0:
            self._raise_command_error(result)
        return result

    @staticmethod
    def _raise_command_error(result: GitCommandResult) -> None:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        message = "Git 操作失败。" if not detail else f"Git 操作失败：{detail[:500]}"
        raise WorktreeGitError("git_command_failed", message)


@dataclass(frozen=True, slots=True)
class ParsedGitStatus:
    head: str | None
    branch: str | None
    upstream: str | None
    staged: tuple[str, ...]
    modified: tuple[str, ...]
    untracked: tuple[str, ...]


def parse_worktree_porcelain(data: bytes) -> tuple[GitWorktreeEntry, ...]:
    entries: list[GitWorktreeEntry] = []
    current: dict[str, object] | None = None
    for raw in data.split(b"\0"):
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace")
        if line.startswith("worktree "):
            if current is not None:
                entries.append(_worktree_entry(current))
            current = {"path": line.removeprefix("worktree "), "locked": False}
        elif current is None:
            raise WorktreeGitError("git_output_invalid", "Git worktree 输出顺序无效。")
        elif line.startswith("HEAD "):
            current["head"] = _validate_oid_value(line.removeprefix("HEAD "))
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line.removeprefix("branch refs/heads/")
        elif line == "locked" or line.startswith("locked "):
            current["locked"] = True
    if current is not None:
        entries.append(_worktree_entry(current))
    return tuple(entries)


def _worktree_entry(data: Mapping[str, object]) -> GitWorktreeEntry:
    path = data.get("path")
    if not isinstance(path, str) or not path:
        raise WorktreeGitError("git_output_invalid", "Git worktree 路径缺失。")
    head = data.get("head")
    branch = data.get("branch")
    return GitWorktreeEntry(
        Path(path).resolve(strict=False),
        head if isinstance(head, str) else None,
        branch if isinstance(branch, str) else None,
        data.get("locked") is True,
    )


def parse_status_porcelain_v2(data: bytes) -> ParsedGitStatus:
    tokens = data.split(b"\0")
    head: str | None = None
    branch: str | None = None
    upstream: str | None = None
    staged: list[str] = []
    modified: list[str] = []
    untracked: list[str] = []
    position = 0
    while position < len(tokens):
        raw = tokens[position]
        position += 1
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace")
        if line.startswith("# branch.oid "):
            value = line.removeprefix("# branch.oid ")
            head = None if value == "(initial)" else _validate_oid_value(value)
            continue
        if line.startswith("# branch.head "):
            value = line.removeprefix("# branch.head ")
            branch = None if value == "(detached)" else value
            continue
        if line.startswith("# branch.upstream "):
            upstream = line.removeprefix("# branch.upstream ")
            continue
        if line.startswith("? "):
            untracked.append(line[2:])
            continue
        if line.startswith("! "):
            continue
        if line.startswith("1 "):
            fields = line.split(" ", 8)
            if len(fields) != 9:
                raise WorktreeGitError("git_output_invalid", "Git status 普通记录无效。")
            _append_xy(fields[1], fields[8], staged, modified)
            continue
        if line.startswith("2 "):
            fields = line.split(" ", 9)
            if len(fields) != 10 or position >= len(tokens):
                raise WorktreeGitError("git_output_invalid", "Git status 重命名记录无效。")
            position += 1
            _append_xy(fields[1], fields[9], staged, modified)
            continue
        if line.startswith("u "):
            fields = line.split(" ", 10)
            if len(fields) != 11:
                raise WorktreeGitError("git_output_invalid", "Git status 冲突记录无效。")
            _append_xy(fields[1], fields[10], staged, modified)
            continue
        if not line.startswith("# "):
            raise WorktreeGitError("git_output_invalid", "Git status 输出包含未知记录。")
    return ParsedGitStatus(head, branch, upstream, tuple(staged), tuple(modified), tuple(untracked))


def _append_xy(
    xy: str,
    path: str,
    staged: list[str],
    modified: list[str],
) -> None:
    if len(xy) != 2:
        raise WorktreeGitError("git_output_invalid", "Git status XY 字段无效。")
    if xy[0] != ".":
        staged.append(path)
    if xy[1] != ".":
        modified.append(path)


def parse_commit_records(data: bytes) -> tuple[WorktreeCommit, ...]:
    parts = [part.strip(b"\r\n") for part in data.split(b"\0")]
    parts = [part for part in parts if part]
    if len(parts) % 2:
        raise WorktreeGitError("git_output_invalid", "Git commit 输出无效。")
    commits: list[WorktreeCommit] = []
    for position in range(0, len(parts), 2):
        oid = _validate_oid_value(parts[position].decode("ascii", errors="strict"))
        subject = parts[position + 1].decode("utf-8", errors="replace")
        commits.append(WorktreeCommit(oid, subject))
    return tuple(commits)


def deletion_decision(
    lifecycle: WorktreeLifecycle,
    status: WorktreeStatusSnapshot,
) -> WorktreeDeleteDecision:
    reasons: list[str] = []
    if lifecycle is WorktreeLifecycle.ACTIVE:
        reasons.append("worktree_active")
    if status.error is not None or status.head is None:
        reasons.append("git_status_unknown")
    if status.dirty:
        reasons.append("worktree_dirty")
    if status.commits:
        if status.upstream is None:
            reasons.append("upstream_missing")
        elif status.unpushed_commits:
            reasons.append("commits_unpushed")
    return WorktreeDeleteDecision(not reasons, tuple(reasons))


def _decode_oid(data: bytes) -> str:
    try:
        return _validate_oid_value(data.decode("ascii").strip())
    except UnicodeError as error:
        raise WorktreeGitError("git_output_invalid", "Git 对象 ID 输出无效。") from error


def _validate_oid_value(value: str) -> str:
    if not _OID_PATTERN.fullmatch(value):
        raise WorktreeGitError("git_output_invalid", "Git 对象 ID 输出无效。")
    return value.lower()


class LinkedWorktreeHeadReader:
    """不启动 Git 进程地验证 linked worktree 并读取 HEAD。"""

    def __init__(self, project_root: str | Path) -> None:
        self._project_root = Path(project_root).resolve(strict=True)
        self._common_git_dir = (self._project_root / ".git").resolve(strict=True)
        if not self._common_git_dir.is_dir():
            raise ValueError("主仓库 .git 必须是目录")
        self._private_root = (self._common_git_dir / "worktrees").resolve(strict=False)

    def read(self, worktree: str | Path, record: WorktreeRecord) -> LinkedWorktreeHead:
        root = Path(worktree).resolve(strict=True)
        if os.path.normcase(str(root)) != os.path.normcase(
            str(Path(record.path).resolve(strict=True))
        ):
            raise WorktreeGitError("fast_recovery_mismatch", "Worktree 路径与管理记录不匹配。")
        pointer = root / ".git"
        try:
            text = pointer.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            raise WorktreeGitError(
                "fast_recovery_invalid", "Worktree .git 指针无法读取。"
            ) from error
        if not text.startswith("gitdir: ") or "\n" in text:
            raise WorktreeGitError("fast_recovery_invalid", "Worktree .git 指针格式无效。")
        private = self._resolve_pointer(pointer.parent, text.removeprefix("gitdir: "))
        self._ensure_within(private, self._private_root)
        if private.parent != self._private_root or not private.is_dir():
            raise WorktreeGitError("fast_recovery_invalid", "Worktree 私有 Git 目录无效。")
        self._validate_backlink(private, pointer)
        common = self._read_commondir(private)
        if os.path.normcase(str(common)) != os.path.normcase(str(self._common_git_dir)):
            raise WorktreeGitError("fast_recovery_mismatch", "Worktree Git common dir 不匹配。")
        branch, oid = self._read_head(private, common)
        if branch is not None and branch != record.branch:
            raise WorktreeGitError("fast_recovery_mismatch", "Worktree 分支与管理记录不匹配。")
        if record.current_head is not None and oid != record.current_head.lower():
            raise WorktreeGitError("fast_recovery_mismatch", "Worktree HEAD 与管理记录不匹配。")
        return LinkedWorktreeHead(branch, oid, private, common)

    def _validate_backlink(self, private: Path, pointer: Path) -> None:
        try:
            text = (private / "gitdir").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            raise WorktreeGitError(
                "fast_recovery_invalid", "Worktree Git 反向指针无法读取。"
            ) from error
        backlink = self._resolve_pointer(private, text)
        if os.path.normcase(str(backlink)) != os.path.normcase(str(pointer.resolve(strict=True))):
            raise WorktreeGitError("fast_recovery_mismatch", "Worktree Git 反向指针不匹配。")

    def _read_commondir(self, private: Path) -> Path:
        try:
            text = (private / "commondir").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            raise WorktreeGitError(
                "fast_recovery_invalid", "Worktree commondir 无法读取。"
            ) from error
        return self._resolve_pointer(private, text)

    def _read_head(self, private: Path, common: Path) -> tuple[str | None, str]:
        try:
            head = (private / "HEAD").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            raise WorktreeGitError("fast_recovery_invalid", "Worktree HEAD 无法读取。") from error
        if head.startswith("ref: "):
            reference = head.removeprefix("ref: ")
            if not reference.startswith("refs/heads/") or ".." in Path(reference).parts:
                raise WorktreeGitError("fast_recovery_invalid", "Worktree HEAD 引用无效。")
            oid = self._read_reference(common, reference)
            return reference.removeprefix("refs/heads/"), oid
        return None, self._validate_oid(head)

    def _read_reference(self, common: Path, reference: str) -> str:
        loose = common.joinpath(*reference.split("/"))
        self._ensure_within(loose.resolve(strict=False), common)
        try:
            if loose.is_file():
                return self._validate_oid(loose.read_text(encoding="utf-8").strip())
            packed = (common / "packed-refs").read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            raise WorktreeGitError(
                "fast_recovery_invalid", "Worktree HEAD 引用无法读取。"
            ) from error
        for line in packed:
            if not line or line.startswith(("#", "^")):
                continue
            try:
                oid, name = line.split(" ", 1)
            except ValueError:
                raise WorktreeGitError("fast_recovery_invalid", "packed-refs 格式无效。") from None
            if name == reference:
                return self._validate_oid(oid)
        raise WorktreeGitError("fast_recovery_invalid", "Worktree HEAD 引用不存在。")

    @staticmethod
    def _resolve_pointer(parent: Path, value: str) -> Path:
        if not value:
            raise WorktreeGitError("fast_recovery_invalid", "Worktree Git 指针为空。")
        target = Path(value)
        try:
            return (target if target.is_absolute() else parent / target).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise WorktreeGitError("fast_recovery_invalid", "Worktree Git 指针无效。") from error

    @staticmethod
    def _ensure_within(path: Path, root: Path) -> None:
        try:
            common = os.path.commonpath((os.path.normcase(str(root)), os.path.normcase(str(path))))
        except ValueError as error:
            raise WorktreeGitError("fast_recovery_invalid", "Worktree Git 路径越界。") from error
        if os.path.normcase(common) != os.path.normcase(str(root)):
            raise WorktreeGitError("fast_recovery_invalid", "Worktree Git 路径越界。")

    @staticmethod
    def _validate_oid(value: str) -> str:
        if not _OID_PATTERN.fullmatch(value):
            raise WorktreeGitError("fast_recovery_invalid", "Worktree HEAD 对象 ID 无效。")
        return value.lower()
