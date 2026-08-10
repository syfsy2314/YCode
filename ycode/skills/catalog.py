"""项目 Skill 目录扫描与事务式状态。"""

from __future__ import annotations

from pathlib import Path

from ycode.skills.loader import SkillLoader
from ycode.skills.models import (
    SkillCatalogEntry,
    SkillCatalogState,
    SkillProblem,
    SkillProblemSeverity,
    SkillSnapshot,
    SkillValidationEnvironment,
)


class SkillCatalogScanError(RuntimeError):
    """Skill 根目录无法完成扫描。"""


class SkillCatalog:
    def __init__(
        self,
        project_root: str | Path,
        loader: SkillLoader,
        environment: SkillValidationEnvironment,
    ) -> None:
        self._project_root = Path(project_root).resolve(strict=True)
        self._skills_root = self._project_root / ".ycode" / "skills"
        self._loader = loader
        self._environment = environment
        self._state = SkillCatalogState()

    @property
    def skills_root(self) -> Path:
        return self._skills_root

    @property
    def state(self) -> SkillCatalogState:
        return self._state

    @property
    def entries(self) -> tuple[SkillCatalogEntry, ...]:
        return self._state.entries

    def get_available(self, name: str) -> SkillSnapshot | None:
        return self._state.available.get(name.lower())

    def get_entry(self, name: str) -> SkillCatalogEntry | None:
        normalized = name.casefold()
        return next(
            (
                entry
                for entry in self.entries
                if entry.directory_name.casefold() == normalized
                or (entry.snapshot is not None and entry.snapshot.name.casefold() == normalized)
            ),
            None,
        )

    def scan_candidate(self) -> SkillCatalogState:
        if not self._skills_root.exists():
            return SkillCatalogState()
        try:
            children = sorted(
                (path for path in self._skills_root.iterdir() if path.is_dir()),
                key=lambda path: (path.name.casefold(), path.name),
            )
        except OSError as error:
            raise SkillCatalogScanError("无法扫描项目 Skill 目录") from error

        entries: list[SkillCatalogEntry] = []
        for child in children:
            source = child / "SKILL.md"
            if not source.is_file():
                entries.append(
                    SkillCatalogEntry(
                        child.name,
                        source,
                        None,
                        (
                            SkillProblem(
                                "skill_file_missing",
                                "Skill 目录缺少 SKILL.md",
                                SkillProblemSeverity.ERROR,
                            ),
                        ),
                    )
                )
                continue
            entries.append(self._loader.load(source, self._environment))
        return SkillCatalogState(self._mark_name_conflicts(entries))

    def commit(self, candidate: SkillCatalogState) -> None:
        if not isinstance(candidate, SkillCatalogState):
            raise TypeError("Skill 目录候选状态无效")
        self._state = candidate

    def set_environment(self, environment: SkillValidationEnvironment) -> None:
        self._environment = environment

    def reload(self) -> SkillCatalogState:
        candidate = self.scan_candidate()
        self.commit(candidate)
        return candidate

    def reload_one(self, name: str) -> SkillCatalogEntry:
        entry = self.get_entry(name)
        if entry is None:
            raise KeyError(name)
        return self._loader.load(entry.source_path, self._environment)

    def catalog_text(self) -> str:
        available = [entry.snapshot for entry in self.entries if entry.snapshot is not None]
        if not available:
            return "No project skills are currently available."
        lines = ["Available project skills:"]
        lines.extend(f"- {snapshot.name}: {snapshot.description}" for snapshot in available)
        return "\n".join(lines)

    @staticmethod
    def _mark_name_conflicts(
        entries: list[SkillCatalogEntry],
    ) -> tuple[SkillCatalogEntry, ...]:
        groups: dict[str, list[int]] = {}
        for index, entry in enumerate(entries):
            if entry.snapshot is not None:
                groups.setdefault(entry.snapshot.name.casefold(), []).append(index)
        for indexes in groups.values():
            if len(indexes) < 2:
                continue
            for index in indexes:
                entry = entries[index]
                entries[index] = SkillCatalogEntry(
                    entry.directory_name,
                    entry.source_path,
                    None,
                    (
                        *entry.problems,
                        SkillProblem(
                            "skill_name_conflict",
                            "多个项目 Skill 使用相同的规范化名称",
                            SkillProblemSeverity.ERROR,
                        ),
                    ),
                )
        return tuple(entries)


__all__ = ["SkillCatalog", "SkillCatalogScanError"]
