"""命令框架的公开错误。"""


class CommandDefinitionError(ValueError):
    """命令定义无效。"""


class CommandConflictError(CommandDefinitionError):
    """命令名称或别名发生冲突。"""


class CommandUsageError(ValueError):
    """命令参数不符合用法。"""


class CommandExecutionError(RuntimeError):
    """可安全展示给用户的命令执行错误。"""

    def __init__(self, summary: str) -> None:
        if not summary.strip():
            raise ValueError("命令执行错误摘要不能为空")
        self.summary = summary
        super().__init__(summary)
