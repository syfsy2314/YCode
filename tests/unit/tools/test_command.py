import asyncio
import csv
import subprocess
from pathlib import Path

import pytest

from ycode.tools import ToolContext, ToolError
from ycode.tools.builtin import RunCommandArguments, RunCommandTool
from ycode.tools.command import CommandRunner, PowerShellCommandRunner
from ycode.tools.paths import WorkspacePathResolver


def process_exists(pid: int) -> bool:
    completed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
    )
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) >= 2 and row[1].isdigit() and int(row[1]) == pid:
            return True
    return False


def resolved(path: Path) -> Path:
    return path.resolve()


async def wait_for_file(path: Path) -> None:
    while True:
        if await asyncio.to_thread(path.is_file):
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_runner_captures_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    runner = PowerShellCommandRunner()

    result = await runner.run(
        "Write-Output 'hello'; [Console]::Error.WriteLine('problem'); exit 7",
        tmp_path,
    )

    assert result.exit_code == 7
    assert result.stdout.strip() == "hello"
    assert result.stderr.strip() == "problem"
    assert result.elapsed_seconds >= 0
    assert not result.truncated


@pytest.mark.asyncio
async def test_runner_uses_process_working_directory(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()

    result = await PowerShellCommandRunner().run("(Get-Location).Path", child)

    assert resolved(Path(result.stdout.strip())) == resolved(child)


@pytest.mark.asyncio
async def test_runner_caps_combined_output_without_deadlock(tmp_path: Path) -> None:
    result = await PowerShellCommandRunner().run(
        "[Console]::Out.Write('o' * 70000); [Console]::Error.Write('e' * 70000)",
        tmp_path,
    )

    assert len(result.stdout.encode()) + len(result.stderr.encode()) == 100 * 1024
    assert result.truncated


@pytest.mark.asyncio
async def test_run_command_tool_returns_structured_nonzero_result(tmp_path: Path) -> None:
    resolver = WorkspacePathResolver(tmp_path)
    tool = RunCommandTool(resolver, PowerShellCommandRunner())

    result = await tool.execute(
        RunCommandArguments(
            command="Write-Output 'before'; [Console]::Error.WriteLine('bad'); exit 3"
        ),
        ToolContext(workspace=resolved(tmp_path)),
    )

    assert result.is_error
    assert result.metadata["exit_code"] == 3
    assert result.metadata["stdout"].strip() == "before"  # type: ignore[union-attr]
    assert result.metadata["stderr"].strip() == "bad"  # type: ignore[union-attr]
    assert result.metadata["cwd"] == "."


@pytest.mark.asyncio
async def test_run_command_rejects_invalid_cwd(tmp_path: Path) -> None:
    tool = RunCommandTool(
        WorkspacePathResolver(tmp_path),
        PowerShellCommandRunner(),
    )

    with pytest.raises(ToolError) as error:
        await tool.execute(
            RunCommandArguments(command="Write-Output ok", cwd="missing"),
            ToolContext(workspace=resolved(tmp_path)),
        )
    assert error.value.code == "path_not_found"


@pytest.mark.asyncio
async def test_cancellation_terminates_powershell_and_child_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids.txt"
    escaped_path = str(pid_file).replace("'", "''")
    command = (
        "$child = Start-Process powershell.exe "
        "-ArgumentList @('-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 30') "
        "-PassThru; "
        f"\"$PID,$($child.Id)\" | Set-Content -Encoding ascii '{escaped_path}'; "
        "Start-Sleep -Seconds 30"
    )
    task = asyncio.create_task(PowerShellCommandRunner().run(command, tmp_path))
    async with asyncio.timeout(5):
        await wait_for_file(pid_file)
    parent_pid, child_pid = [
        int(value) for value in (await asyncio.to_thread(pid_file.read_text)).strip().split(",")
    ]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not await asyncio.to_thread(process_exists, parent_pid)
    assert not await asyncio.to_thread(process_exists, child_pid)


def test_runner_structurally_satisfies_protocol() -> None:
    assert isinstance(PowerShellCommandRunner(), CommandRunner)
