import os
import subprocess
from pathlib import Path

import pytest

from ycode.tools import ToolError
from ycode.tools.paths import WorkspaceMount, WorkspacePathResolver


def test_resolves_relative_and_inside_absolute_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "folder" / "file.txt"
    file_path.parent.mkdir()
    file_path.write_text("ok", encoding="utf-8")
    resolver = WorkspacePathResolver(workspace)

    assert resolver.resolve_existing_file("folder/file.txt") == file_path.resolve()
    assert resolver.resolve_existing_file(file_path.resolve()) == file_path.resolve()
    assert resolver.resolve_existing_directory("folder") == file_path.parent.resolve()
    assert resolver.relative_display(file_path) == "folder/file.txt"


def test_rejects_outside_prefix_and_parent_traversal(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    outside = tmp_path / "work-other"
    workspace.mkdir()
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")
    resolver = WorkspacePathResolver(workspace)

    with pytest.raises(ToolError) as absolute_error:
        resolver.resolve_existing_file(outside_file)
    assert absolute_error.value.code == "path_outside_workspace"

    with pytest.raises(ToolError) as traversal_error:
        resolver.resolve_existing_file("../work-other/secret.txt")
    assert traversal_error.value.code == "path_outside_workspace"


def test_distinguishes_missing_file_and_wrong_target_type(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    directory = workspace / "folder"
    directory.mkdir(parents=True)
    file_path = workspace / "file.txt"
    file_path.write_text("ok", encoding="utf-8")
    resolver = WorkspacePathResolver(workspace)

    with pytest.raises(ToolError) as missing:
        resolver.resolve_existing_file("missing.txt")
    assert missing.value.code == "path_not_found"

    with pytest.raises(ToolError) as directory_error:
        resolver.resolve_existing_file("folder")
    assert directory_error.value.code == "not_a_file"

    with pytest.raises(ToolError) as file_error:
        resolver.resolve_existing_directory("file.txt")
    assert file_error.value.code == "not_a_directory"


def test_resolves_new_write_target_through_real_parent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    parent = workspace / "folder"
    parent.mkdir(parents=True)
    resolver = WorkspacePathResolver(workspace)

    assert resolver.resolve_write_target("folder/new.txt") == parent.resolve() / "new.txt"

    with pytest.raises(ToolError) as missing_parent:
        resolver.resolve_write_target("missing/new.txt")
    assert missing_parent.value.code == "parent_not_found"


def test_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")
    link = workspace / "linked.txt"
    try:
        link.symlink_to(outside_file)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")

    resolver = WorkspacePathResolver(workspace)
    with pytest.raises(ToolError) as error:
        resolver.resolve_existing_file("linked.txt")
    assert error.value.code == "path_outside_workspace"


@pytest.mark.skipif(os.name != "nt", reason="Junction 只在 Windows 验证")
def test_rejects_junction_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    junction = workspace / "junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip("当前环境不允许创建 Junction")

    resolver = WorkspacePathResolver(workspace)
    with pytest.raises(ToolError) as error:
        resolver.resolve_write_target("junction/new.txt")
    assert error.value.code == "path_outside_workspace"


def test_read_only_virtual_mount_allows_reads_but_rejects_writes_and_command_cwd(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "worktree"
    memory = tmp_path / "main-memory"
    workspace.mkdir()
    memory.mkdir()
    topic = memory / "topic.md"
    topic.write_text("memory\n", encoding="utf-8")
    resolver = WorkspacePathResolver(
        workspace,
        mounts=(WorkspaceMount(Path(".ycode/memory"), memory, virtual=True),),
    )

    assert resolver.resolve_existing_file(".ycode/memory/topic.md") == topic.resolve()
    assert resolver.relative_display(topic) == ".ycode/memory/topic.md"
    with pytest.raises(ToolError) as write_error:
        resolver.resolve_write_target(".ycode/memory/new.md")
    assert write_error.value.code == "mount_read_only"
    with pytest.raises(ToolError) as cwd_error:
        resolver.resolve_command_directory(".ycode/memory")
    assert cwd_error.value.code == "mount_cwd_denied"
    with pytest.raises(ToolError) as direct_error:
        resolver.resolve_existing_file(topic.resolve())
    assert direct_error.value.code == "path_outside_workspace"


def test_writable_directory_mount_stays_within_registered_source(tmp_path: Path) -> None:
    workspace = tmp_path / "worktree"
    dependency = tmp_path / "dependency"
    workspace.mkdir()
    dependency.mkdir()
    resolver = WorkspacePathResolver(
        workspace,
        mounts=(
            WorkspaceMount(
                Path("node_modules"),
                dependency,
                writable=True,
                command_cwd_allowed=True,
            ),
        ),
    )

    assert resolver.resolve_write_target("node_modules/new.txt") == dependency.resolve() / "new.txt"
    assert resolver.resolve_command_directory("node_modules") == dependency.resolve()
    with pytest.raises(ToolError) as traversal:
        resolver.resolve_write_target("node_modules/../outside.txt")
    assert traversal.value.code == "path_outside_workspace"
