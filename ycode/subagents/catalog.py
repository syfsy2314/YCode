"""内置与项目子 Agent 角色目录。"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from ycode.subagents.loader import SubagentRoleLoader, normalize_role_name
from ycode.subagents.models import (
    SubagentRoleCatalogEntry,
    SubagentRoleProblem,
    SubagentRoleSnapshot,
    SubagentRoleValidationEnvironment,
)

BUILTIN_ROLE_NAMES = ("explore", "plan")


class SubagentRoleCatalog:
    def __init__(
        self,
        project_root: str | Path,
        loader: SubagentRoleLoader,
        environment: SubagentRoleValidationEnvironment,
    ) -> None:
        self._project_root = Path(project_root).resolve(strict=True)
        self._agents_root = self._project_root / ".ycode" / "agents"
        self._loader = loader
        self._environment = environment
        self._entries: tuple[SubagentRoleCatalogEntry, ...] = ()

    @property
    def entries(self) -> tuple[SubagentRoleCatalogEntry, ...]:
        return self._entries

    @property
    def problems(self) -> tuple[SubagentRoleProblem, ...]:
        return tuple(problem for entry in self._entries for problem in entry.problems)

    def get_available(self, name: str) -> SubagentRoleSnapshot | None:
        normalized = normalize_role_name(name)
        return next(
            (
                entry.role
                for entry in self._entries
                if entry.role is not None and entry.role.config.name == normalized
            ),
            None,
        )

    def load(self) -> tuple[SubagentRoleCatalogEntry, ...]:
        builtins = list(self._load_builtins())
        projects = self._load_projects()
        builtin_names = {
            entry.normalized_name for entry in builtins if entry.normalized_name is not None
        }
        projects = self._mark_conflicts(projects, builtin_names)
        self._entries = tuple(
            sorted(
                (*builtins, *projects),
                key=lambda entry: (
                    entry.normalized_name or "",
                    entry.source.casefold(),
                    entry.source,
                ),
            )
        )
        return self._entries

    def _load_builtins(self) -> tuple[SubagentRoleCatalogEntry, ...]:
        root = files("ycode.subagents.resources")
        entries: list[SubagentRoleCatalogEntry] = []
        for name in BUILTIN_ROLE_NAMES:
            resource = root.joinpath(f"{name}.md")
            entry = self._loader.load_text(
                resource.read_text(encoding="utf-8"),
                source=f"builtin:{name}",
                file_stem=name,
                environment=self._environment,
                builtin=True,
            )
            if entry.role is None:
                raise RuntimeError(f"内置子 Agent 角色无效：{name}")
            entries.append(entry)
        return tuple(entries)

    def _load_projects(self) -> list[SubagentRoleCatalogEntry]:
        if not self._agents_root.exists():
            return []
        try:
            paths = sorted(
                (path for path in self._agents_root.glob("*.md") if path.is_file()),
                key=lambda path: (path.name.casefold(), path.name),
            )
        except OSError:
            return [
                SubagentRoleCatalogEntry(
                    str(self._agents_root),
                    None,
                    None,
                    (
                        SubagentRoleProblem(
                            str(self._agents_root),
                            "role_scan_failed",
                            "无法扫描项目角色目录",
                        ),
                    ),
                )
            ]
        return [self._loader.load(path, self._environment) for path in paths]

    @staticmethod
    def _mark_conflicts(
        entries: list[SubagentRoleCatalogEntry],
        builtin_names: set[str | None],
    ) -> list[SubagentRoleCatalogEntry]:
        groups: dict[str, list[int]] = {}
        for index, entry in enumerate(entries):
            if entry.normalized_name is not None:
                groups.setdefault(entry.normalized_name, []).append(index)
        result = list(entries)
        for name, indexes in groups.items():
            builtin_conflict = name in builtin_names
            duplicate = len(indexes) > 1
            if not builtin_conflict and not duplicate:
                continue
            for index in indexes:
                entry = result[index]
                code = "builtin_role_conflict" if builtin_conflict else "role_name_conflict"
                message = (
                    "项目角色不能覆盖内置角色"
                    if builtin_conflict
                    else "多个项目角色使用相同的规范化名称"
                )
                result[index] = SubagentRoleCatalogEntry(
                    entry.source,
                    entry.normalized_name,
                    None,
                    (*entry.problems, SubagentRoleProblem(entry.source, code, message)),
                )
        return result
