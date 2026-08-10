"""持久化会话的数据模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from ycode.agent.contracts import TurnMessage
from ycode.context.models import ConversationMemory
from ycode.core.messages import ChatMessage
from ycode.errors import YCodeError

SESSION_FORMAT_VERSION = 1
_SESSION_ID = re.compile(r"^\d{8}-\d{6}-[^<>:\"/\\|?*\x00-\x1f.][^<>:\"/\\|?*\x00-\x1f]*$")
_TURN_ID = re.compile(r"^(?!000000)\d{6}$")
_SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def require_utc(value: datetime, name: str = "时间") -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise ValueError(f"{name}必须是 UTC")


def require_session_id(value: str) -> None:
    if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
        raise ValueError("会话 ID 无效")


def require_turn_id(value: str, *, optional: bool = False) -> None:
    if optional and not value:
        return
    if not isinstance(value, str) or not _TURN_ID.fullmatch(value):
        raise ValueError("回合 ID 必须是六位数字")


@dataclass(frozen=True, slots=True)
class SessionMessageRecord:
    version: int
    session_id: str
    turn_id: str
    timestamp: datetime
    message: ChatMessage

    def __post_init__(self) -> None:
        if self.version != SESSION_FORMAT_VERSION:
            raise ValueError("会话记录版本无效")
        require_session_id(self.session_id)
        require_turn_id(self.turn_id)
        require_utc(self.timestamp)
        if not isinstance(self.message, ChatMessage):
            raise TypeError("消息记录必须携带 ChatMessage")


@dataclass(frozen=True, slots=True)
class TurnCommitRecord:
    version: int
    session_id: str
    turn_id: str
    timestamp: datetime
    message_count: int

    def __post_init__(self) -> None:
        if self.version != SESSION_FORMAT_VERSION:
            raise ValueError("会话记录版本无效")
        require_session_id(self.session_id)
        require_turn_id(self.turn_id)
        require_utc(self.timestamp)
        if (
            not isinstance(self.message_count, int)
            or isinstance(self.message_count, bool)
            or self.message_count < 1
        ):
            raise ValueError("提交消息数必须是正整数")


@dataclass(frozen=True, slots=True)
class ContextCheckpointRecord:
    version: int
    session_id: str
    covered_turn_id: str
    timestamp: datetime
    memory: ConversationMemory
    retained_history: tuple[ChatMessage, ...] = ()

    def __post_init__(self) -> None:
        if self.version != SESSION_FORMAT_VERSION:
            raise ValueError("会话记录版本无效")
        require_session_id(self.session_id)
        require_turn_id(self.covered_turn_id)
        require_utc(self.timestamp)
        if not isinstance(self.memory, ConversationMemory):
            raise TypeError("检查点必须携带 ConversationMemory")
        retained = tuple(self.retained_history)
        if any(not isinstance(message, ChatMessage) for message in retained):
            raise TypeError("检查点保留历史只能包含 ChatMessage")
        object.__setattr__(self, "retained_history", retained)


@dataclass(frozen=True, slots=True)
class SkillStateRecord:
    version: int
    session_id: str
    covered_turn_id: str
    timestamp: datetime
    active_skill_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.version != SESSION_FORMAT_VERSION:
            raise ValueError("会话记录版本无效")
        require_session_id(self.session_id)
        require_turn_id(self.covered_turn_id)
        require_utc(self.timestamp)
        names = tuple(self.active_skill_names)
        if any(not _SKILL_NAME.fullmatch(name) or "--" in name for name in names):
            raise ValueError("Skill 状态包含非法名称")
        if len(set(names)) != len(names):
            raise ValueError("Skill 状态名称重复")
        object.__setattr__(self, "active_skill_names", tuple(sorted(names)))


type SessionRecord = (
    SessionMessageRecord | TurnCommitRecord | ContextCheckpointRecord | SkillStateRecord
)


@dataclass(frozen=True, slots=True)
class SessionDescriptor:
    session_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_session_id(self.session_id)
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ValueError("会话创建时间必须带时区")


@dataclass(frozen=True, slots=True)
class SessionWarning:
    code: str
    message: str
    line_number: int | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("会话警告字段不能为空")
        if self.line_number is not None and self.line_number < 1:
            raise ValueError("会话警告行号必须是正整数")


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: str
    history: tuple[ChatMessage, ...]
    memory: ConversationMemory | None
    last_turn_id: str | None
    last_active_at: datetime
    warnings: tuple[SessionWarning, ...] = ()
    active_skill_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_session_id(self.session_id)
        history = tuple(self.history)
        if any(not isinstance(message, ChatMessage) for message in history):
            raise TypeError("会话历史只能包含 ChatMessage")
        object.__setattr__(self, "history", history)
        if self.last_turn_id is not None:
            require_turn_id(self.last_turn_id)
        require_utc(self.last_active_at)
        names = tuple(self.active_skill_names)
        if any(not _SKILL_NAME.fullmatch(name) or "--" in name for name in names):
            raise ValueError("会话快照包含非法 Skill 名称")
        object.__setattr__(self, "active_skill_names", tuple(sorted(set(names))))


@dataclass(frozen=True, slots=True)
class SessionCommit:
    session_id: str
    turn_id: str
    messages: tuple[TurnMessage, ...]

    def __post_init__(self) -> None:
        require_session_id(self.session_id)
        require_turn_id(self.turn_id)
        messages = tuple(self.messages)
        if not messages or any(not isinstance(item, TurnMessage) for item in messages):
            raise ValueError("会话提交必须包含带时间消息")
        object.__setattr__(self, "messages", messages)


class SessionStorageError(YCodeError):
    """会话文件无法安全读取或写入。"""


class SessionNotFoundError(SessionStorageError):
    """指定会话不存在。"""
