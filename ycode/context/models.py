"""上下文管理数据模型。"""

from dataclasses import dataclass, field

from ycode.core.messages import ChatMessage
from ycode.core.provider import AgentModelRequest


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """会话上下文的固定预算策略。"""

    context_window_tokens: int = 200_000
    summary_output_tokens: int = field(default=20_000, init=False)
    safety_margin_tokens: int = field(default=13_000, init=False)
    single_tool_result_bytes: int = field(default=50 * 1024, init=False)
    message_tool_results_bytes: int = field(default=200 * 1024, init=False)
    preview_bytes: int = field(default=4 * 1024, init=False)
    failure_fuse_count: int = field(default=3, init=False)
    stale_session_seconds: int = field(default=24 * 60 * 60, init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.context_window_tokens, int)
            or isinstance(self.context_window_tokens, bool)
            or self.context_window_tokens <= 33_000
        ):
            raise ValueError("上下文窗口必须是大于 33000 的整数")

    @property
    def auto_compact_threshold(self) -> int:
        return self.context_window_tokens - self.summary_output_tokens - self.safety_margin_tokens

    @property
    def continue_request_limit(self) -> int:
        return self.context_window_tokens - self.summary_output_tokens


def _require_non_negative(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name}必须是非负整数")


@dataclass(frozen=True, slots=True)
class ArtifactChunk:
    index: int
    path: str
    bytes: int
    sha256: str

    def __post_init__(self) -> None:
        _require_non_negative(self.index, "分片索引")
        _require_non_negative(self.bytes, "分片字节数")
        if not self.path or not self.sha256:
            raise ValueError("分片路径和哈希不能为空")


@dataclass(frozen=True, slots=True)
class ToolResultManifest:
    version: int
    session_id: str
    tool_name: str
    tool_call_id: str
    is_error: bool
    original_bytes: int
    sha256: str
    chunks: tuple[ArtifactChunk, ...]
    created_at: float

    def __post_init__(self) -> None:
        if self.version < 1 or not self.session_id or not self.tool_name or not self.tool_call_id:
            raise ValueError("工具结果 manifest 标识无效")
        _require_non_negative(self.original_bytes, "工具结果字节数")
        if not self.sha256 or not self.chunks:
            raise ValueError("工具结果 manifest 缺少哈希或分片")
        object.__setattr__(self, "chunks", tuple(self.chunks))


@dataclass(frozen=True, slots=True)
class ToolResultArtifact:
    tool_name: str
    tool_call_id: str
    manifest_path: str
    original_bytes: int
    sha256: str
    preview: str

    def __post_init__(self) -> None:
        _require_non_negative(self.original_bytes, "工具结果字节数")
        if not self.tool_name or not self.tool_call_id or not self.manifest_path or not self.sha256:
            raise ValueError("工具结果引用字段不能为空")


@dataclass(frozen=True, slots=True)
class ContextSessionManifest:
    session_id: str
    process_id: int
    process_started_at: float
    created_at: float

    def __post_init__(self) -> None:
        if not self.session_id or self.process_id < 1:
            raise ValueError("上下文会话标识无效")


@dataclass(frozen=True, slots=True)
class ConversationMemory:
    summary: str

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("会话摘要不能为空")


@dataclass(frozen=True, slots=True)
class ContextCommit:
    history: tuple[ChatMessage, ...]
    memory: ConversationMemory | None

    def __post_init__(self) -> None:
        history = tuple(self.history)
        if any(not isinstance(message, ChatMessage) for message in history):
            raise TypeError("上下文提交历史只能包含 ChatMessage")
        object.__setattr__(self, "history", history)


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    local_tokens: int
    calibrated_tokens: int
    total_tokens: int = field(init=False)

    def __post_init__(self) -> None:
        _require_non_negative(self.local_tokens, "本地 Token 估算")
        _require_non_negative(self.calibrated_tokens, "校准 Token 估算")
        object.__setattr__(self, "total_tokens", max(self.local_tokens, self.calibrated_tokens))


@dataclass(frozen=True, slots=True)
class SummarySource:
    previous_memory: ConversationMemory | None
    messages: tuple[ChatMessage, ...]
    latest_user_message: ChatMessage | None = None

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if any(not isinstance(message, ChatMessage) for message in messages):
            raise TypeError("摘要来源只能包含 ChatMessage")
        if self.latest_user_message is not None and self.latest_user_message.role != "user":
            raise ValueError("摘要保留消息必须来自用户")
        object.__setattr__(self, "messages", messages)


@dataclass(frozen=True, slots=True)
class SummaryResult:
    summary: ConversationMemory
    retained_messages: tuple[ChatMessage, ...] = ()

    def __post_init__(self) -> None:
        retained = tuple(self.retained_messages)
        if any(not isinstance(message, ChatMessage) for message in retained):
            raise TypeError("摘要保留历史只能包含 ChatMessage")
        object.__setattr__(self, "retained_messages", retained)


@dataclass(frozen=True, slots=True)
class ContextCompactionReport:
    before_tokens: int
    after_tokens: int
    manual: bool = False

    def __post_init__(self) -> None:
        _require_non_negative(self.before_tokens, "压缩前 Token")
        _require_non_negative(self.after_tokens, "压缩后 Token")


@dataclass(frozen=True, slots=True)
class ContextFailureReport:
    code: str
    message: str
    failure_count: int
    fuse_open: bool
    request_continues: bool

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("上下文失败报告字段不能为空")
        _require_non_negative(self.failure_count, "连续失败次数")


@dataclass(frozen=True, slots=True)
class PreparedContextRequest:
    request: AgentModelRequest
    messages: tuple[ChatMessage, ...]
    estimate: TokenEstimate
    compaction_report: ContextCompactionReport | None = None
    failure_report: ContextFailureReport | None = None

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if any(not isinstance(message, ChatMessage) for message in messages):
            raise TypeError("预检消息只能包含 ChatMessage")
        object.__setattr__(self, "messages", messages)
