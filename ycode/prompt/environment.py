"""运行环境与 Git 状态摘要采集。"""

import asyncio
import platform
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ycode.prompt.models import SupplementKind, SystemSupplement

GitRunner = Callable[[Path], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class GitStatus:
    branch: str
    staged: int
    modified: int
    untracked: int

    def __post_init__(self) -> None:
        if not isinstance(self.branch, str) or not self.branch.strip():
            raise ValueError("Git 分支不能为空")
        for value in (self.staged, self.modified, self.untracked):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("Git 状态数量必须是非负整数")

    @property
    def dirty(self) -> bool:
        return bool(self.staged or self.modified or self.untracked)


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    workspace: Path
    operating_system: str
    shell: str
    captured_at: datetime
    git: GitStatus | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.workspace, Path) or not self.workspace.is_absolute():
            raise ValueError("环境工作区必须是绝对路径")
        if not self.operating_system.strip() or not self.shell.strip():
            raise ValueError("操作系统和 Shell 不能为空")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("环境快照时间必须包含时区")
        if self.git is not None and not isinstance(self.git, GitStatus):
            raise TypeError("Git 状态必须是 GitStatus")

    @property
    def content(self) -> str:
        timezone_name = self.captured_at.tzname() or "unknown"
        lines = [
            f"Workspace: {self.workspace}",
            f"Operating system: {self.operating_system}",
            f"Shell: {self.shell}",
            f"Local time: {self.captured_at.isoformat(timespec='seconds')}",
            f"Time zone: {timezone_name}",
        ]
        if self.git is not None:
            state = "dirty" if self.git.dirty else "clean"
            lines.extend(
                [
                    f"Git branch: {self.git.branch}",
                    (
                        f"Git status: {state} "
                        f"(staged={self.git.staged}, modified={self.git.modified}, "
                        f"untracked={self.git.untracked})"
                    ),
                ]
            )
        return "\n".join(lines)

    def to_supplement(self) -> SystemSupplement:
        return SystemSupplement(SupplementKind.ENVIRONMENT, self.content)


def _parse_git_status(output: str) -> GitStatus | None:
    lines = [line for line in output.splitlines() if line]
    if not lines or not lines[0].startswith("## "):
        return None

    header = lines[0][3:]
    if header.startswith("No commits yet on "):
        branch = header.removeprefix("No commits yet on ")
    elif header.startswith("Initial commit on "):
        branch = header.removeprefix("Initial commit on ")
    elif header.startswith("HEAD (no branch)"):
        branch = "HEAD (detached)"
    else:
        branch = header.split("...", maxsplit=1)[0]
    branch = branch.strip()
    if not branch:
        return None

    staged = 0
    modified = 0
    untracked = 0
    for line in lines[1:]:
        if len(line) < 2:
            continue
        status = line[:2]
        if status == "??":
            untracked += 1
            continue
        if status[0] not in {" ", "?"}:
            staged += 1
        if status[1] not in {" ", "?"}:
            modified += 1
    return GitStatus(branch, staged, modified, untracked)


async def _run_git_status(workspace: Path, timeout_seconds: float = 2.0) -> str | None:
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(workspace),
            "status",
            "--porcelain=v1",
            "--branch",
            "--untracked-files=all",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            return None
    except OSError:
        return None

    if process.returncode != 0:
        return None
    return stdout.decode("utf-8", errors="replace")


class EnvironmentCollector:
    def __init__(
        self,
        workspace: Path,
        shell: str = "PowerShell",
        *,
        git_runner: GitRunner | None = None,
    ) -> None:
        if not isinstance(workspace, Path):
            raise TypeError("环境工作区必须是 Path")
        if not isinstance(shell, str) or not shell.strip():
            raise ValueError("Shell 不能为空")
        self._workspace = workspace.resolve()
        self._shell = shell
        self._git_runner = git_runner or _run_git_status

    async def collect(self, *, now: datetime | None = None) -> EnvironmentSnapshot:
        captured_at = now or datetime.now().astimezone()
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            captured_at = captured_at.astimezone()

        git: GitStatus | None = None
        try:
            output = await self._git_runner(self._workspace)
            if output is not None:
                git = _parse_git_status(output)
        except Exception:
            git = None

        return EnvironmentSnapshot(
            workspace=self._workspace,
            operating_system=platform.system() or "unknown",
            shell=self._shell,
            captured_at=captured_at,
            git=git,
        )
