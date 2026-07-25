"""YCode 的用户可见错误边界。"""


class YCodeError(Exception):
    """所有可预期 YCode 错误的基类。"""


class ConfigError(YCodeError):
    """配置发现、解析或校验失败。"""


class ProviderError(YCodeError):
    """供应商调用失败，字符串表示只包含安全提示。"""

    def __init__(self, code: str, user_message: str, retryable: bool) -> None:
        self.code = code
        self.user_message = user_message
        self.retryable = retryable
        super().__init__(user_message)


class MessageAssemblyError(YCodeError):
    """供应商流无法安全组装为完整消息。"""


class UIError(YCodeError):
    """终端界面初始化或渲染失败。"""
