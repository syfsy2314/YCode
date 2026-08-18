"""Worktree 本地环境初始化。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ycode.config.models import WorktreeConfig
from ycode.worktrees.git import GitWorktreeClient, WorktreeGitError


class WorktreeInitializationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class InitializationWarning:
    code: str
    path: str
    message: str

    def render(self) -> str:
        return f"{self.code}: {self.path}: {self.message}"


@dataclass(frozen=True, slots=True)
class InitializedDirectoryLink:
    relative_path: str
    source: Path
    target: Path


@dataclass(frozen=True, slots=True)
class WorktreeInitializationResult:
    warnings: tuple[InitializationWarning, ...]
    links: tuple[InitializedDirectoryLink, ...]
    git_environment: Mapping[str, str]
    hooks_path: Path
    custom_hooks: bool

    @property
    def warning_messages(self) -> tuple[str, ...]:
        return tuple(warning.render() for warning in self.warnings)


DirectoryLinker = Callable[[Path, Path], Awaitable[None]]


def git_config_environment(key: str, value: str) -> dict[str, str]:
    raw_count = os.environ.get("GIT_CONFIG_COUNT", "0")
    try:
        count = int(raw_count)
    except ValueError as error:
        raise WorktreeInitializationError(
            "hooks_environment_invalid", "现有 Git 配置环境无法安全扩展。"
        ) from error
    if count < 0:
        raise WorktreeInitializationError(
            "hooks_environment_invalid", "现有 Git 配置环境无法安全扩展。"
        )
    return {
        "GIT_CONFIG_COUNT": str(count + 1),
        f"GIT_CONFIG_KEY_{count}": key,
        f"GIT_CONFIG_VALUE_{count}": value,
    }


class WorktreeInitializer:
    def __init__(
        self,
        project_root: str | Path,
        config: WorktreeConfig,
        git: GitWorktreeClient,
        *,
        directory_linker: DirectoryLinker | None = None,
    ) -> None:
        self._project_root = Path(project_root).resolve(strict=True)
        self._config = config
        self._git = git
        self._directory_linker = directory_linker or _link_directory

    async def initialize(self, worktree: str | Path) -> WorktreeInitializationResult:
        root = _resolve_path(worktree, strict=True)
        hooks_path, git_environment, custom_hooks = await self._resolve_hooks(root)
        warnings: list[InitializationWarning] = []
        links: list[InitializedDirectoryLink] = []

        for relative in self._config.copy_files:
            warning = await asyncio.to_thread(self._copy_relative_file, root, relative)
            if warning is not None:
                warnings.append(warning)

        for pattern in self._config.ignored_file_globs:
            try:
                matches = await asyncio.to_thread(self._expand_glob, pattern)
            except OSError:
                warnings.append(
                    InitializationWarning("glob_failed", pattern, "无法展开忽略文件 Glob")
                )
                continue
            for source in matches:
                relative = source.relative_to(self._project_root).as_posix()
                try:
                    ignored = await self._git.is_ignored(relative)
                except WorktreeGitError:
                    warnings.append(
                        InitializationWarning(
                            "ignore_check_failed", relative, "无法确认文件是否被 Git 忽略"
                        )
                    )
                    continue
                if not ignored:
                    warnings.append(
                        InitializationWarning("not_ignored", relative, "匹配文件未被 Git 忽略")
                    )
                    continue
                warning = await asyncio.to_thread(
                    self._copy_relative_file,
                    root,
                    relative,
                )
                if warning is not None:
                    warnings.append(warning)

        for relative in self._config.link_directories:
            source, target, warning = await asyncio.to_thread(
                self._prepare_link,
                root,
                relative,
            )
            if warning is not None:
                warnings.append(warning)
                continue
            assert source is not None and target is not None
            try:
                await self._directory_linker(source, target)
            except (OSError, RuntimeError):
                warnings.append(
                    InitializationWarning("link_failed", relative, "依赖目录链接创建失败")
                )
                continue
            links.append(InitializedDirectoryLink(relative, source, target))

        return WorktreeInitializationResult(
            tuple(warnings),
            tuple(links),
            git_environment,
            hooks_path,
            custom_hooks,
        )

    async def _resolve_hooks(
        self,
        worktree: Path,
    ) -> tuple[Path, Mapping[str, str], bool]:
        try:
            configured = await self._git.config_path(self._project_root, "core.hooksPath")
            if configured is None:
                main_hooks = await self._git.git_path(self._project_root, "hooks")
                child_hooks = await self._git.git_path(worktree, "hooks")
                if _path_key(main_hooks) != _path_key(child_hooks):
                    raise WorktreeInitializationError(
                        "hooks_mismatch", "主仓库与 Worktree 的默认 Git Hooks 目录不一致。"
                    )
                return main_hooks, {}, False
            main_hooks = _effective_path(self._project_root, configured)
            environment = git_config_environment("core.hooksPath", str(main_hooks))
            child_git = self._git.with_environment(environment)
            child_config = await child_git.config_path(worktree, "core.hooksPath")
            if child_config is None:
                raise WorktreeInitializationError(
                    "hooks_mismatch", "Worktree 未继承主仓库 Git Hooks 配置。"
                )
            child_hooks = _effective_path(worktree, child_config)
            if _path_key(main_hooks) != _path_key(child_hooks):
                raise WorktreeInitializationError(
                    "hooks_mismatch", "主仓库与 Worktree 的 Git Hooks 目录不一致。"
                )
            return main_hooks, environment, True
        except WorktreeInitializationError:
            raise
        except (OSError, UnicodeError, WorktreeGitError) as error:
            raise WorktreeInitializationError(
                "hooks_check_failed", "无法确认主仓库与 Worktree 的 Git Hooks 目录。"
            ) from error

    def _copy_relative_file(
        self,
        worktree: Path,
        relative: str,
    ) -> InitializationWarning | None:
        if PurePosixPath(relative).as_posix().casefold() == ".ycode/config.yaml":
            return InitializationWarning("source_reserved", relative, "项目主配置不复制到 Worktree")
        source = self._project_root.joinpath(*PurePosixPath(relative).parts)
        target = worktree.joinpath(*PurePosixPath(relative).parts)
        try:
            resolved_source = source.resolve(strict=True)
            _ensure_within(resolved_source, self._project_root)
            if source.is_symlink() or not resolved_source.is_file():
                return InitializationWarning("source_invalid", relative, "源不是普通文件")
            if target.exists() or target.is_symlink():
                return InitializationWarning("target_exists", relative, "目标已存在，未覆盖")
            target.parent.mkdir(parents=True, exist_ok=True)
            resolved_parent = target.parent.resolve(strict=True)
            _ensure_within(resolved_parent, worktree)
            shutil.copy2(resolved_source, resolved_parent / target.name)
        except FileNotFoundError:
            return InitializationWarning("source_missing", relative, "源文件不存在")
        except (OSError, RuntimeError):
            return InitializationWarning("copy_failed", relative, "本地文件复制失败")
        return None

    def _expand_glob(self, pattern: str) -> tuple[Path, ...]:
        matches: list[Path] = []
        for source in self._project_root.glob(pattern):
            try:
                resolved = source.resolve(strict=True)
                _ensure_within(resolved, self._project_root)
            except (OSError, RuntimeError, ValueError):
                continue
            if source.is_symlink() or not resolved.is_file():
                continue
            matches.append(source)
        return tuple(sorted(matches))

    def _prepare_link(
        self,
        worktree: Path,
        relative: str,
    ) -> tuple[Path | None, Path | None, InitializationWarning | None]:
        source = self._project_root.joinpath(*PurePosixPath(relative).parts)
        target = worktree.joinpath(*PurePosixPath(relative).parts)
        try:
            resolved_source = source.resolve(strict=True)
            _ensure_within(resolved_source, self._project_root)
            if source.is_symlink() or not resolved_source.is_dir():
                return (
                    None,
                    None,
                    InitializationWarning("link_source_invalid", relative, "依赖源不是普通目录"),
                )
            if target.exists() or target.is_symlink():
                return (
                    None,
                    None,
                    InitializationWarning("target_exists", relative, "目标已存在，未覆盖"),
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            _ensure_within(target.parent.resolve(strict=True), worktree)
            return resolved_source, target, None
        except FileNotFoundError:
            return (
                None,
                None,
                InitializationWarning("link_source_missing", relative, "依赖源目录不存在"),
            )
        except (OSError, RuntimeError):
            return (
                None,
                None,
                InitializationWarning("link_failed", relative, "依赖目录链接准备失败"),
            )


async def _link_directory(source: Path, target: Path) -> None:
    if os.name != "nt":
        await asyncio.to_thread(os.symlink, source, target, True)
        return
    environment = {
        **os.environ,
        "YCODE_JUNCTION_SOURCE": str(source),
        "YCODE_JUNCTION_TARGET": str(target),
    }
    script = (
        "$ErrorActionPreference='Stop'; "
        "New-Item -ItemType Junction -Path $env:YCODE_JUNCTION_TARGET "
        "-Target $env:YCODE_JUNCTION_SOURCE | Out-Null"
    )
    process = await asyncio.create_subprocess_exec(
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script,
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if await process.wait() != 0:
        raise OSError("Junction 创建失败")


def _resolve_path(value: str | Path, *, strict: bool) -> Path:
    return Path(value).resolve(strict=strict)


def _effective_path(root: Path, value: str) -> Path:
    path = Path(value)
    return _resolve_path(path if path.is_absolute() else root / path, strict=False)


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path))


def _ensure_within(path: Path, root: Path) -> None:
    try:
        common = os.path.commonpath((_path_key(root), _path_key(path)))
    except ValueError as error:
        raise OSError("路径越界") from error
    if os.path.normcase(common) != _path_key(root):
        raise OSError("路径越界")
