"""固定 PowerShell 后端的异步命令执行。"""

import asyncio
import os
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

_MAX_OUTPUT_BYTES = 100 * 1024
_READ_SIZE = 8192
_POWERSHELL_UTF8_PREFIX = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    truncated: bool

    def __post_init__(self) -> None:
        if not isinstance(self.exit_code, int) or isinstance(self.exit_code, bool):
            raise TypeError("命令退出码必须是整数")
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise TypeError("命令输出必须是字符串")
        if (
            not isinstance(self.elapsed_seconds, int | float)
            or isinstance(self.elapsed_seconds, bool)
            or self.elapsed_seconds < 0
        ):
            raise ValueError("命令耗时必须是非负数")
        if not isinstance(self.truncated, bool):
            raise TypeError("命令截断标记必须是布尔值")


@runtime_checkable
class CommandRunner(Protocol):
    async def run(self, command: str, cwd: Path) -> CommandResult: ...


class _OutputBudget:
    def __init__(self, maximum_bytes: int) -> None:
        self.remaining = maximum_bytes
        self.truncated = False
        self.lock = asyncio.Lock()

    async def retain(self, chunk: bytes) -> bytes:
        async with self.lock:
            if len(chunk) <= self.remaining:
                self.remaining -= len(chunk)
                return chunk
            retained = chunk[: self.remaining]
            self.remaining = 0
            self.truncated = True
            return retained


class PowerShellCommandRunner:
    """使用 powershell.exe 执行命令并在取消时终止完整进程树。"""

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = dict(environment or {})

    async def run(self, command: str, cwd: Path) -> CommandResult:
        if not command:
            raise ValueError("PowerShell 命令不能为空")
        started_at = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"{_POWERSHELL_UTF8_PREFIX}{command}",
            cwd=str(cwd),
            env={**os.environ, **self._environment},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        collection = asyncio.create_task(self._collect(process))
        try:
            stdout, stderr, truncated = await asyncio.shield(collection)
        except asyncio.CancelledError:
            await self._terminate_process_tree(process)
            try:
                await asyncio.shield(collection)
            except Exception:
                pass
            raise

        return CommandResult(
            exit_code=process.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            elapsed_seconds=time.perf_counter() - started_at,
            truncated=truncated,
        )

    async def _collect(
        self,
        process: asyncio.subprocess.Process,
    ) -> tuple[bytes, bytes, bool]:
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("PowerShell 输出管道未创建")
        budget = _OutputBudget(_MAX_OUTPUT_BYTES)
        stdout_task = asyncio.create_task(self._drain(process.stdout, budget))
        stderr_task = asyncio.create_task(self._drain(process.stderr, budget))
        await process.wait()
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        return stdout, stderr, budget.truncated

    @staticmethod
    async def _drain(
        stream: asyncio.StreamReader,
        budget: _OutputBudget,
    ) -> bytes:
        retained: list[bytes] = []
        while chunk := await stream.read(_READ_SIZE):
            selected = await budget.retain(chunk)
            if selected:
                retained.append(selected)
        return b"".join(retained)

    @staticmethod
    async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            await asyncio.wait_for(killer.wait(), timeout=5)
        except (OSError, TimeoutError):
            if process.returncode is None:
                process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
