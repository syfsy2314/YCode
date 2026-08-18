"""统一管理项目内持久化会话。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ycode.agent.contracts import TurnMessage
from ycode.context.models import ConversationMemory
from ycode.core.messages import ChatMessage, TextBlock, ToolCallBlock, ToolResultBlock
from ycode.session.codec import SessionCodecError, decode_record, encode_record
from ycode.session.models import (
    SESSION_FORMAT_VERSION,
    ContextCheckpointRecord,
    SessionCommit,
    SessionDescriptor,
    SessionMessageRecord,
    SessionNotFoundError,
    SessionSnapshot,
    SessionStorageError,
    SessionWarning,
    SkillStateRecord,
    TurnCommitRecord,
    require_session_id,
)

_INVALID_TITLE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACE = re.compile(r"\s+")


class SessionManager:
    """提供会话创建、提交、恢复、列表和删除的唯一磁盘入口。"""

    def __init__(
        self,
        project_root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = Path(project_root).resolve(strict=True)
        self._sessions_root = self._root / ".ycode" / "sessions"
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._active_session_id: str | None = None
        self._last_turn_number = 0
        self._pending_session_id: str | None = None

    @property
    def sessions_root(self) -> Path:
        return self._sessions_root

    @property
    def active_session_id(self) -> str | None:
        return self._active_session_id

    @property
    def current_session_id(self) -> str | None:
        """返回已提交或已预留的稳定会话 ID。"""
        return self._active_session_id or self._pending_session_id

    def reserve_session_id(self, first_message: ChatMessage) -> str:
        """为首次回合预留会话 ID，但不提前创建空会话文件。"""
        if not isinstance(first_message, ChatMessage) or first_message.role != "user":
            raise ValueError("只能使用用户消息预留会话 ID")
        if self._active_session_id is not None:
            return self._active_session_id
        if self._pending_session_id is None:
            self._pending_session_id = self._new_session_id(first_message)
        return self._pending_session_id

    def begin_new(self) -> None:
        self._active_session_id = None
        self._last_turn_number = 0
        self._pending_session_id = None

    def activate(self, snapshot: SessionSnapshot) -> None:
        self._active_session_id = snapshot.session_id
        self._last_turn_number = int(snapshot.last_turn_id or "0")
        self._pending_session_id = None

    async def commit_turn(
        self,
        messages: Sequence[TurnMessage],
        *,
        checkpoint: tuple[ConversationMemory, tuple[ChatMessage, ...]] | None = None,
        active_skill_names: Sequence[str] | None = None,
    ) -> SessionCommit:
        items = tuple(messages)
        if not items or any(not isinstance(item, TurnMessage) for item in items):
            raise ValueError("会话提交必须包含带时间消息")
        if not _valid_complete_turn(tuple(item.message for item in items)):
            raise ValueError("会话提交消息不是完整结构回合")
        skill_names = None if active_skill_names is None else tuple(active_skill_names)
        return await asyncio.to_thread(self._commit_turn, items, checkpoint, skill_names)

    async def append_skill_state(
        self,
        active_skill_names: Sequence[str],
    ) -> SkillStateRecord:
        if self._active_session_id is None or self._last_turn_number < 1:
            raise SessionStorageError("当前没有可写入 Skill 状态的会话")
        record = SkillStateRecord(
            SESSION_FORMAT_VERSION,
            self._active_session_id,
            f"{self._last_turn_number:06d}",
            self._utc_now(),
            tuple(active_skill_names),
        )
        await asyncio.to_thread(
            self._append_lines,
            self._path(self._active_session_id),
            (encode_record(record),),
        )
        return record

    async def append_checkpoint(
        self,
        memory: ConversationMemory,
        retained_history: tuple[ChatMessage, ...],
        *,
        session_id: str | None = None,
        covered_turn_id: str | None = None,
    ) -> ContextCheckpointRecord:
        target_id = session_id or self._active_session_id
        if target_id is None:
            raise SessionStorageError("当前没有可写入检查点的会话")
        target_turn = covered_turn_id or f"{self._last_turn_number:06d}"
        record = ContextCheckpointRecord(
            SESSION_FORMAT_VERSION,
            target_id,
            target_turn,
            self._utc_now(),
            memory,
            retained_history,
        )
        await asyncio.to_thread(self._append_lines, self._path(target_id), (encode_record(record),))
        return record

    async def load(self, session_id: str) -> SessionSnapshot:
        require_session_id(session_id)
        return await asyncio.to_thread(self._load, session_id)

    async def load_latest(self) -> SessionSnapshot:
        sessions = await self.list_sessions()
        if not sessions:
            raise SessionNotFoundError("没有可恢复的会话")
        return await self.load(sessions[0].session_id)

    async def list_sessions(self) -> tuple[SessionDescriptor, ...]:
        return await asyncio.to_thread(self._list_sessions)

    async def delete(self, session_id: str) -> None:
        require_session_id(session_id)
        if session_id == self._active_session_id:
            raise SessionStorageError("不能删除当前活动会话")
        path = self._path(session_id)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError as error:
            raise SessionNotFoundError("指定会话不存在") from error
        except OSError as error:
            raise SessionStorageError("会话删除失败") from error

    def _commit_turn(
        self,
        messages: tuple[TurnMessage, ...],
        checkpoint: tuple[ConversationMemory, tuple[ChatMessage, ...]] | None,
        active_skill_names: tuple[str, ...] | None,
    ) -> SessionCommit:
        session_id = self._active_session_id
        if session_id is None:
            session_id = self._pending_session_id or self._new_session_id(messages[0].message)
            self._pending_session_id = session_id
        turn_number = self._last_turn_number + 1
        turn_id = f"{turn_number:06d}"
        lines = [
            encode_record(
                SessionMessageRecord(
                    SESSION_FORMAT_VERSION,
                    session_id,
                    turn_id,
                    item.created_at,
                    item.message,
                )
            )
            for item in messages
        ]
        if checkpoint is not None:
            memory, retained = checkpoint
            lines.append(
                encode_record(
                    ContextCheckpointRecord(
                        SESSION_FORMAT_VERSION,
                        session_id,
                        turn_id,
                        self._utc_now(),
                        memory,
                        retained,
                    )
                )
            )
        if active_skill_names is not None:
            lines.append(
                encode_record(
                    SkillStateRecord(
                        SESSION_FORMAT_VERSION,
                        session_id,
                        turn_id,
                        self._utc_now(),
                        active_skill_names,
                    )
                )
            )
        lines.append(
            encode_record(
                TurnCommitRecord(
                    SESSION_FORMAT_VERSION,
                    session_id,
                    turn_id,
                    self._utc_now(),
                    len(messages),
                )
            )
        )
        self._append_lines(self._path(session_id), tuple(lines))
        self._active_session_id = session_id
        self._pending_session_id = None
        self._last_turn_number = turn_number
        return SessionCommit(session_id, turn_id, messages)

    def _append_lines(self, path: Path, lines: tuple[str, ...]) -> None:
        self._sessions_root.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("a+b") as stream:
                stream.seek(0, 2)
                start = stream.tell()
                try:
                    for line in lines:
                        stream.write(line.encode("utf-8") + b"\n")
                    stream.flush()
                except BaseException:
                    stream.seek(start)
                    stream.truncate()
                    stream.flush()
                    raise
        except OSError as error:
            raise SessionStorageError("会话写入失败") from error

    def _load(self, session_id: str) -> SessionSnapshot:
        path = self._path(session_id)
        if not path.is_file():
            raise SessionNotFoundError("指定会话不存在")
        warnings: list[SessionWarning] = []
        history: list[ChatMessage] = []
        pending: list[SessionMessageRecord] = []
        pending_checkpoint: ContextCheckpointRecord | None = None
        pending_skill_state: SkillStateRecord | None = None
        memory: ConversationMemory | None = None
        active_skill_names: tuple[str, ...] = ()
        last_turn_id: str | None = None
        last_active_at: datetime | None = None
        safe_offset = 0
        structural_error = False

        try:
            with path.open("rb") as stream:
                line_number = 0
                while raw := stream.readline():
                    line_number += 1
                    end_offset = stream.tell()
                    try:
                        record = decode_record(raw.decode("utf-8"))
                    except (UnicodeError, SessionCodecError):
                        warnings.append(
                            SessionWarning("invalid_json", "已跳过无法解析的会话记录", line_number)
                        )
                        continue
                    if record.session_id != session_id:
                        structural_error = True
                        break
                    expected_turn = f"{int(last_turn_id or '0') + 1:06d}"
                    if isinstance(record, SessionMessageRecord):
                        if record.turn_id != expected_turn:
                            structural_error = True
                            break
                        pending.append(record)
                    elif isinstance(record, ContextCheckpointRecord):
                        if pending and record.covered_turn_id == expected_turn:
                            pending_checkpoint = record
                        elif (
                            not pending
                            and last_turn_id is not None
                            and record.covered_turn_id <= last_turn_id
                        ):
                            memory = record.memory
                            history = list(record.retained_history)
                            safe_offset = end_offset
                        else:
                            structural_error = True
                            break
                    elif isinstance(record, SkillStateRecord):
                        if pending and record.covered_turn_id == expected_turn:
                            pending_skill_state = record
                        elif (
                            not pending
                            and last_turn_id is not None
                            and record.covered_turn_id == last_turn_id
                        ):
                            active_skill_names = record.active_skill_names
                            last_active_at = record.timestamp
                            safe_offset = end_offset
                        else:
                            structural_error = True
                            break
                    else:
                        messages = tuple(item.message for item in pending)
                        if (
                            record.turn_id != expected_turn
                            or record.message_count != len(pending)
                            or not _valid_complete_turn(messages)
                        ):
                            structural_error = True
                            break
                        if pending_checkpoint is not None:
                            memory = pending_checkpoint.memory
                            history = list(pending_checkpoint.retained_history)
                        else:
                            history.extend(messages)
                        last_turn_id = record.turn_id
                        last_active_at = record.timestamp
                        if pending_skill_state is not None:
                            active_skill_names = pending_skill_state.active_skill_names
                        safe_offset = end_offset
                        pending.clear()
                        pending_checkpoint = None
                        pending_skill_state = None
                file_size = stream.tell()
        except OSError as error:
            raise SessionStorageError("会话读取失败") from error

        if (
            structural_error
            or pending
            or pending_skill_state is not None
            or file_size > safe_offset
        ):
            try:
                with path.open("r+b") as stream:
                    stream.truncate(safe_offset)
            except OSError as error:
                raise SessionStorageError("会话修复失败") from error
            warnings.append(SessionWarning("repaired_tail", "已截断不完整或结构无效的会话尾部"))
        if last_turn_id is None or last_active_at is None:
            raise SessionStorageError("会话中没有完整提交")
        return SessionSnapshot(
            session_id,
            tuple(history),
            memory,
            last_turn_id,
            last_active_at,
            tuple(warnings),
            active_skill_names,
        )

    def _list_sessions(self) -> tuple[SessionDescriptor, ...]:
        if not self._sessions_root.exists():
            return ()
        descriptors: list[SessionDescriptor] = []
        for path in self._sessions_root.glob("*.jsonl"):
            session_id = path.stem
            try:
                require_session_id(session_id)
                created = datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S").replace(
                    tzinfo=datetime.now().astimezone().tzinfo
                )
                descriptors.append(SessionDescriptor(session_id, created))
            except ValueError:
                continue
        return tuple(sorted(descriptors, key=lambda item: item.session_id, reverse=True))

    def _new_session_id(self, first_message: ChatMessage) -> str:
        title = _SPACE.sub(" ", first_message.text).strip()
        title = _INVALID_TITLE.sub("", title).strip(" .")[:32].strip()
        title = title or "session"
        timestamp = self._clock().strftime("%Y%m%d-%H%M%S")
        base = f"{timestamp}-{title}"
        candidate = base
        suffix = 2
        while self._path(candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _path(self, session_id: str) -> Path:
        require_session_id(session_id)
        return self._sessions_root / f"{session_id}.jsonl"

    def _utc_now(self) -> datetime:
        value = self._clock().astimezone(UTC)
        return value


def _valid_complete_turn(messages: tuple[ChatMessage, ...]) -> bool:
    if len(messages) < 2 or messages[0].role != "user" or messages[-1].role != "assistant":
        return False
    expected_results: set[str] | None = None
    for position, message in enumerate(messages):
        if expected_results is not None:
            if message.role != "user" or any(
                not isinstance(block, ToolResultBlock) for block in message.content
            ):
                return False
            results = message.blocks(ToolResultBlock)
            actual = {block.tool_call_id for block in results}
            if actual != expected_results or len(actual) != len(results):
                return False
            expected_results = None
            continue
        if message.role == "user":
            if position != 0 or any(not isinstance(block, TextBlock) for block in message.content):
                return False
            continue
        calls = message.blocks(ToolCallBlock)
        if calls:
            expected_results = {call.id for call in calls}
            if len(expected_results) != len(calls):
                return False
        elif position != len(messages) - 1:
            return False
    return expected_results is None
