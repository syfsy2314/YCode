from pathlib import Path

import pytest

from ycode.config.discovery import discover_config
from ycode.errors import ConfigError


def test_finds_nearest_config_from_child_directory(tmp_path: Path) -> None:
    root_config = tmp_path / ".ycode" / "config.yaml"
    root_config.parent.mkdir()
    root_config.write_text("active: root", encoding="utf-8")
    project = tmp_path / "project"
    nearest = project / ".ycode" / "config.yaml"
    nearest.parent.mkdir(parents=True)
    nearest.write_text("active: nearest", encoding="utf-8")
    child = project / "a" / "b"
    child.mkdir(parents=True)

    assert discover_config(start_dir=child) == nearest


def test_explicit_path_bypasses_search(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.yaml"
    explicit.write_text("active: explicit", encoding="utf-8")
    assert discover_config(explicit, start_dir=tmp_path / "unused") == explicit.resolve()


def test_missing_explicit_path_reports_resolved_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ConfigError, match="指定的配置文件不存在") as caught:
        discover_config(missing)
    assert str(missing.resolve()) in str(caught.value)


def test_search_failure_reports_start_and_target(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as caught:
        discover_config(start_dir=tmp_path)
    message = str(caught.value)
    assert str(tmp_path.resolve()) in message
    assert ".ycode/config.yaml" in message
