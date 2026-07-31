"""使用 PowerShell AST 解析并分类高危命令。"""

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

_PARSER_SCRIPT = r"""
$source = [Console]::In.ReadToEnd()
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
$commands = @(
    $ast.FindAll(
        { param($node) $node -is [System.Management.Automation.Language.CommandAst] },
        $true
    ) | ForEach-Object {
        $pipeline = $_
        while ($null -ne $pipeline -and
               $pipeline -isnot [System.Management.Automation.Language.PipelineAst]) {
            $pipeline = $pipeline.Parent
        }
        [ordered]@{
            name = $_.GetCommandName()
            elements = @($_.CommandElements | ForEach-Object { $_.Extent.Text })
            pipeline = if ($null -eq $pipeline) { $_.Extent.Text } else {
                $pipeline.Extent.Text
            }
        }
    }
)
[ordered]@{
    errors = @($errors | ForEach-Object { $_.Message })
    commands = $commands
} | ConvertTo-Json -Depth 6 -Compress
"""


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    name: str
    elements: tuple[str, ...]
    pipeline: str


@dataclass(frozen=True, slots=True)
class CommandSafetyResult:
    safe: bool
    category: str = ""
    message: str = ""


class PowerShellSafetyChecker:
    def __init__(
        self,
        workspace: Path,
        *,
        executable: str = "powershell.exe",
        timeout_seconds: float = 5.0,
    ) -> None:
        self._workspace = workspace.resolve()
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    async def check(self, command: str) -> CommandSafetyResult:
        try:
            commands = await self.parse(command)
        except (OSError, ValueError, TimeoutError):
            return CommandSafetyResult(
                safe=False,
                category="parser_failure",
                message="无法可靠解析命令，已拒绝执行。",
            )
        return self._classify(commands)

    async def parse(self, command: str) -> tuple[ParsedCommand, ...]:
        if not command.strip():
            raise ValueError("命令不能为空")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = await asyncio.create_subprocess_exec(
            self._executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _PARSER_SCRIPT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(command.encode("utf-8")),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            raise ValueError("PowerShell 解析进程失败")
        try:
            payload = json.loads(stdout.decode("utf-8-sig"))
            errors = payload["errors"]
            raw_commands = payload["commands"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError("PowerShell 解析结果无效") from error
        if not isinstance(errors, list) or not isinstance(raw_commands, list):
            raise ValueError("PowerShell 解析结果结构无效")
        if errors:
            raise ValueError("PowerShell 命令存在语法错误")

        parsed: list[ParsedCommand] = []
        for item in raw_commands:
            if not isinstance(item, dict):
                raise ValueError("PowerShell 命令节点无效")
            name = item.get("name")
            elements = item.get("elements")
            pipeline = item.get("pipeline")
            if (
                not isinstance(name, str)
                or not isinstance(elements, list)
                or any(not isinstance(element, str) for element in elements)
                or not isinstance(pipeline, str)
            ):
                raise ValueError("PowerShell 命令节点字段无效")
            parsed.append(
                ParsedCommand(
                    name=name.casefold(),
                    elements=tuple(element.strip() for element in elements),
                    pipeline=pipeline,
                )
            )
        return tuple(parsed)

    def _classify(
        self,
        commands: tuple[ParsedCommand, ...],
    ) -> CommandSafetyResult:
        pipelines: dict[str, set[str]] = {}
        for parsed in commands:
            pipelines.setdefault(parsed.pipeline, set()).add(_command_name(parsed.name))
        downloaders = {
            "invoke-webrequest",
            "iwr",
            "invoke-restmethod",
            "irm",
            "curl",
            "wget",
        }
        for names in pipelines.values():
            if names & downloaders and names & {"invoke-expression", "iex"}:
                return _danger("remote_execute", "禁止下载远程内容后直接执行。")

        for parsed in commands:
            name = _command_name(parsed.name)
            arguments = tuple(_normalize_element(item) for item in parsed.elements[1:])
            if name in {"invoke-expression", "iex"}:
                return _danger("dynamic_execution", "禁止动态执行命令文本。")
            if name in {"powershell", "pwsh"} and any(
                argument in {"-encodedcommand", "-enc", "-e"} for argument in arguments
            ):
                return _danger("encoded_execution", "禁止执行编码命令。")
            if name in {
                "clear-disk",
                "format-volume",
                "initialize-disk",
                "diskpart",
                "format",
            }:
                return _danger("disk_damage", "禁止磁盘或文件系统破坏操作。")
            if name in {
                "stop-computer",
                "restart-computer",
                "shutdown",
                "bcdedit",
            }:
                return _danger("system_control", "禁止关机或启动配置破坏操作。")
            if name == "git" and _dangerous_git(arguments):
                return _danger("destructive_git", "禁止高破坏性 Git 操作。")
            if name in {"remove-item", "rm", "del", "erase", "rd", "rmdir"} and (
                self._dangerous_delete(name, arguments)
            ):
                return _danger("broad_delete", "禁止大范围破坏性删除。")
            if name in {"takeown", "icacls"} and self._outside_workspace(arguments):
                return _danger(
                    "ownership_or_acl",
                    "禁止修改工作区外的所有权或访问控制。",
                )

        return CommandSafetyResult(safe=True)

    def _dangerous_delete(self, name: str, arguments: tuple[str, ...]) -> bool:
        recursive = any(
            item in {"-recurse", "-r", "/s"} or (name in {"rd", "rmdir"} and "/s" in item)
            for item in arguments
        )
        force = any(
            item in {"-force", "-f", "/q"} or (name in {"rd", "rmdir"} and "/q" in item)
            for item in arguments
        )
        targets = [item for item in arguments if not item.startswith(("-", "/"))]
        return recursive and force and any(self._is_broad_target(item) for item in targets)

    def _is_broad_target(self, value: str) -> bool:
        normalized = value.strip("\"'").replace("/", "\\").casefold()
        broad_literals = {
            "\\",
            "~",
            "$home",
            "$env:userprofile",
            "$env:systemroot",
            "$env:windir",
            "c:\\",
        }
        if normalized in broad_literals:
            return True
        try:
            path = Path(value.strip("\"'")).expanduser()
            return path.is_absolute() and path.resolve() in {
                self._workspace,
                self._workspace.anchor and Path(self._workspace.anchor),
                Path.home().resolve(),
            }
        except OSError:
            return True

    def _outside_workspace(self, arguments: tuple[str, ...]) -> bool:
        for argument in arguments:
            if argument.startswith(("-", "/")):
                continue
            raw = argument.strip("\"'")
            lowered = raw.casefold()
            if lowered.startswith(("$env:", "$home", "~")):
                return True
            path = Path(raw)
            if not path.is_absolute():
                continue
            try:
                path.resolve().relative_to(self._workspace)
            except (OSError, ValueError):
                return True
        return False


def _command_name(value: str) -> str:
    name = Path(value.strip("\"'")).name.casefold()
    return name.removesuffix(".exe").removesuffix(".com")


def _normalize_element(value: str) -> str:
    return value.strip().strip("\"'").casefold()


def _dangerous_git(arguments: tuple[str, ...]) -> bool:
    if not arguments:
        return False
    if arguments[0] == "reset" and "--hard" in arguments:
        return True
    if arguments[0] == "clean" and any(
        item == "--force" or item.startswith("-") and "f" in item[1:] for item in arguments[1:]
    ):
        return True
    if arguments[0] in {"checkout", "restore"} and "." in arguments[1:]:
        return True
    return False


def _danger(category: str, message: str) -> CommandSafetyResult:
    return CommandSafetyResult(safe=False, category=category, message=message)
