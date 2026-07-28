"""可安全返回模型的受控工具错误。"""

from collections.abc import Mapping

from ycode.core.messages import FrozenJsonObject, freeze_json


class ToolError(Exception):
    """表示具体工具预期内且可供模型调整的失败。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        metadata: FrozenJsonObject | None = None,
    ) -> None:
        if not code:
            raise ValueError("工具错误码不能为空")
        if not message:
            raise ValueError("工具错误消息不能为空")

        frozen = freeze_json(metadata or {})
        if not isinstance(frozen, Mapping):
            raise TypeError("工具错误元信息必须是 JSON object")

        super().__init__(message)
        self.code = code
        self.message = message
        self.metadata = frozen
