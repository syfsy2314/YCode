"""工作区边界内的统一路径解析。"""

import os
from pathlib import Path

from ycode.tools.errors import ToolError

PathInput = str | Path


class WorkspacePathResolver:
    """把工具路径解析为已验证的工作区内真实路径。"""

    def __init__(self, workspace: PathInput) -> None:
        root = Path(workspace)
        try:
            resolved = root.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError("工作区路径无法解析") from error
        if not resolved.is_dir():
            raise ValueError("工作区必须是现有目录")
        self._workspace = resolved
        self._workspace_key = os.path.normcase(str(resolved))

    @property
    def workspace(self) -> Path:
        return self._workspace

    def resolve_existing_file(self, path: PathInput) -> Path:
        resolved = self._resolve_existing(path)
        if not resolved.is_file():
            raise ToolError("not_a_file", "目标不是普通文件。")
        return resolved

    def resolve_existing_directory(self, path: PathInput) -> Path:
        resolved = self._resolve_existing(path)
        if not resolved.is_dir():
            raise ToolError("not_a_directory", "目标不是目录。")
        return resolved

    def resolve_write_target(self, path: PathInput) -> Path:
        candidate = self._candidate(path)
        try:
            if candidate.exists() or candidate.is_symlink():
                resolved = candidate.resolve(strict=True)
                self._ensure_within_workspace(resolved)
                return resolved

            parent = candidate.parent.resolve(strict=True)
            self._ensure_within_workspace(parent)
            return parent / candidate.name
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
        self._ensure_within_workspace(resolved)
        relative = resolved.relative_to(self._workspace)
        return relative.as_posix() or "."

    def _resolve_existing(self, path: PathInput) -> Path:
        candidate = self._candidate(path)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise ToolError("path_not_found", "目标路径不存在。") from error
        except RuntimeError as error:
            raise ToolError("invalid_path", "路径包含无法解析的链接。") from error
        except OSError as error:
            raise ToolError("invalid_path", "目标路径无法解析。") from error
        self._ensure_within_workspace(resolved)
        return resolved

    def _candidate(self, path: PathInput) -> Path:
        if not isinstance(path, str | Path):
            raise TypeError("工具路径必须是字符串或 Path")
        candidate = Path(path)
        if not str(candidate):
            raise ToolError("invalid_path", "路径不能为空。")
        return candidate if candidate.is_absolute() else self._workspace / candidate

    def _ensure_within_workspace(self, path: Path) -> None:
        target_key = os.path.normcase(str(path))
        try:
            common = os.path.commonpath((self._workspace_key, target_key))
        except ValueError as error:
            raise ToolError("path_outside_workspace", "目标路径位于工作区之外。") from error
        if os.path.normcase(common) != self._workspace_key:
            raise ToolError("path_outside_workspace", "目标路径位于工作区之外。")
