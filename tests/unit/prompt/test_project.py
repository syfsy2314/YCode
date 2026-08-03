from pathlib import Path

import pytest

from ycode.errors import ConfigError
from ycode.prompt import ProjectContextLoader, SupplementKind, SupplementScope


def test_project_loader_expands_nested_relative_includes(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "YCODE.md").write_text("root\n@include docs/style.md\nend", encoding="utf-8")
    (docs / "style.md").write_text("style\n@include detail.md", encoding="utf-8")
    (docs / "detail.md").write_text("detail", encoding="utf-8")

    snapshot = ProjectContextLoader(tmp_path).load()

    supplement = snapshot.supplements[0]
    assert supplement.kind is SupplementKind.PROJECT_INSTRUCTIONS
    assert supplement.scope is SupplementScope.SESSION
    assert supplement.content == "root\nstyle\ndetail\nend"


def test_project_loader_injects_only_valid_memory_index(tmp_path: Path) -> None:
    memory = tmp_path / ".ycode" / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text(
        "- [项目知识](project-stack.md) — 技术栈\nbad\n",
        encoding="utf-8",
    )
    (memory / "project-stack.md").write_text(
        "---\nname: 项目知识\ndescription: 技术栈\ntype: project_knowledge\n---\nPython 3.12\n",
        encoding="utf-8",
    )

    snapshot = ProjectContextLoader(tmp_path).load()

    assert snapshot.supplements[0].kind is SupplementKind.PROJECT_MEMORY
    assert "project-stack.md" in snapshot.supplements[0].content
    assert "Python 3.12" not in snapshot.supplements[0].content
    assert len(snapshot.warnings) == 1


def test_project_loader_allows_missing_files(tmp_path: Path) -> None:
    snapshot = ProjectContextLoader(tmp_path).load()
    assert snapshot.supplements == ()
    assert snapshot.warnings == ()


@pytest.mark.parametrize("target", ["missing.md", "../outside.md", "C:/outside.md"])
def test_project_loader_rejects_missing_or_escaping_include(tmp_path: Path, target: str) -> None:
    (tmp_path / "YCODE.md").write_text(f"@include {target}", encoding="utf-8")
    with pytest.raises(ConfigError):
        ProjectContextLoader(tmp_path).load()


def test_project_loader_rejects_include_cycle(tmp_path: Path) -> None:
    (tmp_path / "YCODE.md").write_text("@include a.md", encoding="utf-8")
    (tmp_path / "a.md").write_text("@include YCODE.md", encoding="utf-8")
    with pytest.raises(ConfigError, match="循环"):
        ProjectContextLoader(tmp_path).load()


def test_project_loader_rejects_more_than_five_include_levels(tmp_path: Path) -> None:
    (tmp_path / "YCODE.md").write_text("@include level-1.md", encoding="utf-8")
    for level in range(1, 7):
        content = f"@include level-{level + 1}.md" if level < 6 else "too deep"
        (tmp_path / f"level-{level}.md").write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match="深度"):
        ProjectContextLoader(tmp_path).load()
