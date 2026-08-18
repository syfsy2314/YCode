"""YCode 首期内置命令。"""

from collections.abc import Iterable

from ycode.commands.contracts import (
    CommandDefinition,
    CommandInvocation,
    CommandKind,
    CommandRuntime,
    UIController,
)
from ycode.commands.dispatcher import CommandDispatcher
from ycode.commands.errors import CommandUsageError
from ycode.commands.registry import CommandRegistry


def _no_arguments(invocation: CommandInvocation) -> None:
    if invocation.arguments:
        raise CommandUsageError


def _one_argument(invocation: CommandInvocation, *, required: bool) -> str | None:
    arguments = invocation.arguments.strip()
    if not arguments:
        if required:
            raise CommandUsageError
        return None
    if any(character.isspace() for character in arguments):
        raise CommandUsageError
    return arguments


def _format_detail(definition: CommandDefinition) -> str:
    aliases = ", ".join(f"/{alias}" for alias in definition.aliases) or "无"
    hint = definition.argument_hint or "无"
    return (
        f"/{definition.name} — {definition.description}\n"
        f"用法：{definition.usage}\n别名：{aliases}\n参数：{hint}"
    )


def build_command_runtime(
    extra_definitions: Iterable[CommandDefinition] = (),
) -> CommandRuntime:
    registry = CommandRegistry()

    async def help_handler(invocation: CommandInvocation, controller: UIController) -> None:
        target = _one_argument(invocation, required=False)
        if target is None:
            lines = ["可用命令："]
            for definition in registry.visible_definitions():
                aliases = (
                    f"（别名：{', '.join(f'/{alias}' for alias in definition.aliases)}）"
                    if definition.aliases
                    else ""
                )
                lines.append(f"  /{definition.name} {aliases} — {definition.description}")
            await controller.show_system_message("\n".join(lines))
            return
        definition = registry.resolve(target.removeprefix("/"))
        if definition is None or definition.hidden:
            await controller.show_system_message("未知命令。使用 /help 查看可用命令。")
            return
        await controller.show_system_message(_format_detail(definition))

    async def exit_handler(invocation: CommandInvocation, controller: UIController) -> None:
        _no_arguments(invocation)
        await controller.request_exit()

    async def plan_handler(invocation: CommandInvocation, controller: UIController) -> None:
        _no_arguments(invocation)
        await controller.set_mode("plan-only")
        await controller.refresh_status()

    async def agent_handler(invocation: CommandInvocation, controller: UIController) -> None:
        _no_arguments(invocation)
        await controller.set_mode("agent")
        await controller.refresh_status()

    async def mcp_handler(invocation: CommandInvocation, controller: UIController) -> None:
        _no_arguments(invocation)
        await controller.show_mcp_status()

    async def compact_handler(invocation: CommandInvocation, controller: UIController) -> None:
        _no_arguments(invocation)
        await controller.compact_context()
        await controller.refresh_status()

    async def permission_handler(invocation: CommandInvocation, controller: UIController) -> None:
        argument = _one_argument(invocation, required=False)
        if argument is None:
            await controller.show_permission_status()
            return
        argument = argument.lower()
        if argument == "clear":
            await controller.clear_permission_grants()
        elif argument in {"strict", "default", "allow"}:
            await controller.set_permission_mode(argument)
        else:
            raise CommandUsageError
        await controller.refresh_status()

    async def resume_handler(invocation: CommandInvocation, controller: UIController) -> None:
        session_id = invocation.arguments.strip()
        if not session_id:
            raise CommandUsageError
        await controller.resume_session(session_id)
        await controller.refresh_status()

    async def skills_handler(invocation: CommandInvocation, controller: UIController) -> None:
        parts = invocation.arguments.split()
        if not parts:
            await controller.show_skills()
        elif len(parts) == 1 and parts[0] != "reload":
            await controller.show_skill(parts[0])
        elif parts == ["reload"]:
            await controller.reload_skills()
        elif len(parts) == 2 and parts[0] == "deactivate":
            await controller.deactivate_skill(parts[1])
        else:
            raise CommandUsageError
        await controller.refresh_status()

    async def clear_handler(invocation: CommandInvocation, controller: UIController) -> None:
        _no_arguments(invocation)
        await controller.clear_session()
        await controller.refresh_status()

    async def tasks_handler(invocation: CommandInvocation, controller: UIController) -> None:
        parts = invocation.arguments.split()
        if not parts:
            await controller.show_tasks()
        elif len(parts) == 1:
            await controller.show_tasks(parts[0])
        elif len(parts) == 2 and parts[0].lower() == "stop":
            await controller.stop_task(parts[1])
        else:
            raise CommandUsageError

    async def worktree_handler(invocation: CommandInvocation, controller: UIController) -> None:
        parts = invocation.arguments.split()
        if parts in (["list"], ["cleanup"]):
            await controller.manage_worktrees(parts[0])
        elif len(parts) == 2 and parts[0] == "status":
            await controller.manage_worktrees("status", parts[1])
        elif len(parts) == 2 and parts[0] == "delete":
            await controller.manage_worktrees("delete", parts[1])
        elif len(parts) == 3 and parts[0] == "delete" and parts[2] == "--force":
            await controller.manage_worktrees("delete", parts[1], force=True)
        else:
            raise CommandUsageError

    definitions = (
        CommandDefinition(
            "help",
            (),
            "显示命令列表或详细帮助",
            "/help [command]",
            CommandKind.LOCAL,
            "[command]",
            help_handler,
        ),
        CommandDefinition(
            "exit", ("quit",), "退出 YCode", "/exit", CommandKind.LOCAL, "", exit_handler
        ),
        CommandDefinition(
            "plan", (), "切换到计划模式", "/plan", CommandKind.STATE, "", plan_handler
        ),
        CommandDefinition(
            "agent", (), "切换到执行模式", "/agent", CommandKind.STATE, "", agent_handler
        ),
        CommandDefinition(
            "mcp", (), "显示 MCP 连接状态", "/mcp", CommandKind.LOCAL, "", mcp_handler
        ),
        CommandDefinition(
            "compact", (), "压缩当前对话上下文", "/compact", CommandKind.STATE, "", compact_handler
        ),
        CommandDefinition(
            "permission",
            (),
            "查看或修改权限模式",
            "/permission [strict|default|allow|clear]",
            CommandKind.STATE,
            "[strict|default|allow|clear]",
            permission_handler,
        ),
        CommandDefinition(
            "resume",
            (),
            "恢复指定会话",
            "/resume <session-id>",
            CommandKind.STATE,
            "<session-id>",
            resume_handler,
        ),
        CommandDefinition(
            "skills",
            (),
            "查看或管理项目 Skill",
            "/skills [name|reload|deactivate <name>]",
            CommandKind.STATE,
            "[name|reload|deactivate <name>]",
            skills_handler,
        ),
        CommandDefinition(
            "clear", (), "清空当前会话", "/clear", CommandKind.STATE, "", clear_handler
        ),
        CommandDefinition(
            "tasks",
            (),
            "查看或终止子 Agent 任务",
            "/tasks [task-id|stop <task-id>]",
            CommandKind.LOCAL,
            "[task-id|stop <task-id>]",
            tasks_handler,
        ),
        CommandDefinition(
            "worktree",
            (),
            "查看、删除或清理子 Agent Worktree",
            "/worktree <list|status <name>|delete <name> [--force]|cleanup>",
            CommandKind.LOCAL,
            "<list|status|delete|cleanup>",
            worktree_handler,
        ),
    )
    for definition in (*definitions, *tuple(extra_definitions)):
        registry.register(definition)
    return CommandRuntime(registry, CommandDispatcher(registry))
