"""命令解析、查找和安全分发。"""

import asyncio

from ycode.commands.contracts import CommandKind, UIController
from ycode.commands.errors import CommandExecutionError, CommandUsageError
from ycode.commands.parser import CommandParser
from ycode.commands.registry import CommandRegistry


class CommandDispatcher:
    def __init__(self, registry: CommandRegistry, parser: CommandParser | None = None) -> None:
        self._registry = registry
        self._parser = parser or CommandParser()

    async def try_dispatch(self, text: str, controller: UIController) -> bool:
        invocation = self._parser.parse(text)
        if invocation is None:
            return False
        definition = self._registry.resolve(invocation.name)
        if definition is None:
            await controller.show_user_input(invocation.raw_text)
            await controller.show_system_message("未知命令。使用 /help 查看可用命令。")
            return True
        if definition.kind is not CommandKind.AI:
            await controller.show_user_input(invocation.raw_text)
        try:
            await definition.handler(invocation, controller)
        except CommandUsageError:
            await controller.show_system_message(f"参数错误。用法：{definition.usage}")
        except CommandExecutionError as error:
            await controller.show_system_message(error.summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            await controller.show_system_message("命令执行失败，请稍后重试。")
        return True
