"""工作区边界内的统一路径解析。"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Protocol

from ycode.tools.errors import ToolError

PathInput = str | Path


class PathOperation(StrEnum):
    READ = "read"
    WRITE = "write"
    SEARCH = "search"
    COMMAND_CWD = "command_cwd"


class WorkspacePathPolicy(Protocol):
    def check(
        self,
        lexical_path: Path,
        resolved_path: Path | None,
        operation: PathOperation,
    ) -> None: ...

    def excluded_directories(self, search_root: Path) -> tuple[Path, ...]: ...


@dataclass(frozen=True, slots=True)
class WorkspaceMount:
    logical_root: Path
    physical_root: Path
    writable: bool = False
    command_cwd_allowed: bool = False
    virtual: bool = False


class WorkspacePathResolver:
    """把工具路径解析为已验证的工作区路径或受控挂载路径。"""

    def __init__(
        self,
        workspace: PathInput,
        *,
        policy: WorkspacePathPolicy | None = None,
        mounts: Sequence[WorkspaceMount] = (),
    ) -> None:
        root = Path(workspace)
        try:
            resolved = root.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError("工作区路径无法解析") from error
        if not resolved.is_dir():
            raise ValueError("工作区必须是现有目录")
        self._workspace = resolved
        self._workspace_key = _path_key(resolved)
        self._policy = policy
        self._mounts = tuple(self._validate_mount(item) for item in mounts)

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def mounts(self) -> tuple[WorkspaceMount, ...]:
        return self._mounts

    def resolve_existing_file(
        self,
        path: PathInput,
        *,
        operation: PathOperation = PathOperation.READ,
    ) -> Path:
        resolved = self._resolve_existing(path, operation)
        if not resolved.is_file():
            raise ToolError("not_a_file", "目标不是普通文件。")
        return resolved

    def resolve_existing_directory(
        self,
        path: PathInput,
        *,
        operation: PathOperation = PathOperation.READ,
    ) -> Path:
        resolved = self._resolve_existing(path, operation)
        if not resolved.is_dir():
            raise ToolError("not_a_directory", "目标不是目录。")
        return resolved

    def resolve_command_directory(self, path: PathInput) -> Path:
        return self.resolve_existing_directory(path, operation=PathOperation.COMMAND_CWD)

    def resolve_write_target(self, path: PathInput) -> Path:
        candidate = self._candidate(path)
        self._check_policy(candidate, None, PathOperation.WRITE)
        mount, mapped = self._map_candidate(candidate, PathOperation.WRITE)
        try:
            if mapped.exists() or mapped.is_symlink():
                resolved = mapped.resolve(strict=True)
                self._ensure_allowed(resolved, mount)
                self._check_policy(candidate, resolved, PathOperation.WRITE)
                return resolved

            parent = mapped.parent.resolve(strict=True)
            self._ensure_allowed(parent, mount)
            target = parent / mapped.name
            self._check_policy(candidate, target, PathOperation.WRITE)
            return target
        except FileNotFoundError as error:
            raise ToolError("parent_not_found", "目标父目录不存在。") from error
        except RuntimeError as error:
            raise ToolError("invalid_path", "路径包含无法解析的链接。") from error
        except OSError as error:
            raise ToolError("invalid_path", "目标路径无法解析。") from error

    def relative_display(self, path: PathInput) -> str:
        candidate = Path(path)
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise ToolError("invalid_path", "路径无法转换为工作区相对路径。") from error
        if self._is_within(resolved, self._workspace):
            relative = resolved.relative_to(self._workspace)
            return relative.as_posix() or "."
        for mount in self._mounts:
            if self._is_within(resolved, mount.physical_root):
                relative = resolved.relative_to(mount.physical_root)
                return (mount.logical_root / relative).as_posix()
        raise ToolError("path_outside_workspace", "目标路径位于工作区之外。")

    def search_exclusions(self, start: Path) -> tuple[Path, ...]:
        if self._policy is None:
            return ()
        return self._policy.excluded_directories(start)

    def virtual_search_roots(self, start: Path) -> tuple[Path, ...]:
        if _path_key(start) != self._workspace_key:
            return ()
        return tuple(mount.physical_root for mount in self._mounts if mount.virtual)

    def resolve_discovered_file(self, path: PathInput) -> Path:
        candidate = Path(path)
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ToolError("invalid_path", "搜索结果路径无法解析。") from error
        if self._is_within(resolved, self._workspace):
            return self.resolve_existing_file(resolved, operation=PathOperation.SEARCH)
        for mount in self._mounts:
            if self._is_within(resolved, mount.physical_root) and resolved.is_file():
                return resolved
        raise ToolError("path_outside_workspace", "搜索结果位于工作区之外。")

    def _resolve_existing(self, path: PathInput, operation: PathOperation) -> Path:
        candidate = self._candidate(path)
        self._check_policy(candidate, None, operation)
        mount, mapped = self._map_candidate(candidate, operation)
        try:
            resolved = mapped.resolve(strict=True)
        except FileNotFoundError as error:
            raise ToolError("path_not_found", "目标路径不存在。") from error
        except RuntimeError as error:
            raise ToolError("invalid_path", "路径包含无法解析的链接。") from error
        except OSError as error:
            raise ToolError("invalid_path", "目标路径无法解析。") from error
        self._ensure_allowed(resolved, mount)
        self._check_policy(candidate, resolved, operation)
        return resolved

    def _candidate(self, path: PathInput) -> Path:
        if not isinstance(path, str | Path):
            raise TypeError("工具路径必须是字符串或 Path")
        candidate = Path(path)
        if not str(candidate):
            raise ToolError("invalid_path", "路径不能为空。")
        return candidate if candidate.is_absolute() else self._workspace / candidate

    def _map_candidate(
        self,
        candidate: Path,
        operation: PathOperation,
    ) -> tuple[WorkspaceMount | None, Path]:
        for mount in self._mounts:
            logical = self._workspace / mount.logical_root
            if not self._is_within(candidate, logical):
                continue
            if operation is PathOperation.WRITE and not mount.writable:
                raise ToolError("mount_read_only", "目标位于只读工作区挂载。")
            if operation is PathOperation.COMMAND_CWD and not mount.command_cwd_allowed:
                raise ToolError("mount_cwd_denied", "该工作区挂载不能作为命令目录。")
            relative = candidate.relative_to(logical)
            return mount, mount.physical_root / relative
        return None, candidate

    def _ensure_allowed(self, path: Path, mount: WorkspaceMount | None) -> None:
        root = mount.physical_root if mount is not None else self._workspace
        if not self._is_within(path, root):
            raise ToolError("path_outside_workspace", "目标路径位于工作区之外。")

    def _check_policy(
        self,
        lexical_path: Path,
        resolved_path: Path | None,
        operation: PathOperation,
    ) -> None:
        if self._policy is not None:
            self._policy.check(lexical_path, resolved_path, operation)

    def _validate_mount(self, mount: WorkspaceMount) -> WorkspaceMount:
        logical = PurePosixPath(mount.logical_root.as_posix())
        if (
            logical.is_absolute()
            or not logical.parts
            or any(part in {"", ".", ".."} for part in logical.parts)
        ):
            raise ValueError("工作区挂载逻辑路径无效")
        physical = mount.physical_root.resolve(strict=True)
        if not physical.is_dir():
            raise ValueError("工作区挂载物理路径必须是现有目录")
        return WorkspaceMount(
            Path(*logical.parts),
            physical,
            mount.writable,
            mount.command_cwd_allowed,
            mount.virtual,
        )

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            common = os.path.commonpath((_path_key(root), _path_key(path)))
        except ValueError:
            return False
        return os.path.normcase(common) == _path_key(root)


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path))
