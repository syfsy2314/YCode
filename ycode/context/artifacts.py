"""工具结果的会话级临时存储。"""

import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from ycode.config import SecretRedactor
from ycode.context.models import (
    ArtifactChunk,
    ContextPolicy,
    ContextSessionManifest,
    ToolResultArtifact,
    ToolResultManifest,
)
from ycode.core.messages import (
    ChatMessage,
    ToolCallBlock,
    ToolResultBlock,
    thaw_json,
)
from ycode.tools.contracts import ToolExecutionRecord


class ContextStorageError(Exception):
    """上下文文件无法安全写入。"""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _split_utf8(value: str, limit: int) -> tuple[bytes, ...]:
    remaining = value.encode("utf-8")
    chunks: list[bytes] = []
    while remaining:
        end = min(limit, len(remaining))
        while end > 0:
            try:
                remaining[:end].decode("utf-8")
                break
            except UnicodeDecodeError:
                end -= 1
        chunks.append(remaining[:end])
        remaining = remaining[end:]
    return tuple(chunks) or (b"",)


def _preview(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    marker = "\n…\n"
    marker_bytes = len(marker.encode("utf-8"))
    head_limit = min(3 * 1024, limit - marker_bytes)
    tail_limit = limit - marker_bytes - head_limit
    head = _split_utf8(value, head_limit)[0].decode("utf-8")
    tail_bytes = encoded[-tail_limit:] if tail_limit else b""
    while tail_bytes and tail_bytes[0] & 0xC0 == 0x80:
        tail_bytes = tail_bytes[1:]
    return head + marker + tail_bytes.decode("utf-8")


def _process_alive(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


class ContextArtifactStore:
    """管理当前进程的一份上下文临时目录。"""

    def __init__(
        self,
        workspace: str | Path,
        redactor: SecretRedactor,
        policy: ContextPolicy,
        *,
        session_id: str | None = None,
        clock: Callable[[], float] = time.time,
        process_alive: Callable[[int], bool] = _process_alive,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / ".ycode" / "context"
        self.redactor = redactor
        self.policy = policy
        self._clock = clock
        self._process_alive = process_alive
        self.session_id = session_id or f"{os.getpid()}-{uuid.uuid4().hex}"
        self.session_dir = self.root / self.session_id
        self._closed = False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._cleanup_stale_sessions()
            self.session_dir.mkdir(parents=False, exist_ok=False)
            manifest = ContextSessionManifest(
                self.session_id,
                os.getpid(),
                self._clock(),
                self._clock(),
            )
            (self.session_dir / "session.json").write_bytes(_json_bytes(asdict(manifest)))
            (self.session_dir / "tool-results").mkdir()
        except (OSError, ValueError, TypeError) as error:
            raise ContextStorageError("无法初始化上下文临时目录。") from error

    def store(
        self,
        tool_name: str,
        tool_call_id: str,
        content: str,
        *,
        is_error: bool = False,
    ) -> ToolResultArtifact:
        if self._closed:
            raise ContextStorageError("上下文临时目录已关闭。")
        safe_content = self.redactor.redact_text(content)
        content_bytes = safe_content.encode("utf-8")
        content_hash = _sha256(content_bytes)
        artifact_id = hashlib.sha256(f"{tool_call_id}\0{content_hash}".encode()).hexdigest()[:24]
        results_dir = self.session_dir / "tool-results"
        final_dir = results_dir / artifact_id
        manifest_path = final_dir / "manifest.json"
        relative_manifest = manifest_path.relative_to(self.workspace).as_posix()
        if final_dir.is_dir():
            return ToolResultArtifact(
                tool_name,
                tool_call_id,
                relative_manifest,
                len(content_bytes),
                content_hash,
                _preview(safe_content, self.policy.preview_bytes),
            )

        temporary_dir = results_dir / f".tmp-{uuid.uuid4().hex}"
        try:
            chunks_dir = temporary_dir / "chunks"
            chunks_dir.mkdir(parents=True)
            chunks: list[ArtifactChunk] = []
            for index, chunk in enumerate(
                _split_utf8(safe_content, self.policy.single_tool_result_bytes),
                start=1,
            ):
                chunk_path = chunks_dir / f"{index:06d}.txt"
                chunk_path.write_bytes(chunk)
                relative_chunk = chunk_path.relative_to(temporary_dir).as_posix()
                chunks.append(ArtifactChunk(index, relative_chunk, len(chunk), _sha256(chunk)))

            manifest = ToolResultManifest(
                1,
                self.session_id,
                tool_name,
                tool_call_id,
                is_error,
                len(content_bytes),
                content_hash,
                tuple(chunks),
                self._clock(),
            )
            manifest_bytes = _json_bytes(asdict(manifest))
            if len(manifest_bytes) > self.policy.single_tool_result_bytes:
                raise ContextStorageError("工具结果 manifest 超过大小限制。")
            (temporary_dir / "manifest.json").write_bytes(manifest_bytes)
            if (
                b"".join((temporary_dir / chunk.path).read_bytes() for chunk in chunks)
                != content_bytes
            ):
                raise ContextStorageError("工具结果分片校验失败。")
            temporary_dir.replace(final_dir)
        except ContextStorageError:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise
        except (OSError, ValueError, TypeError) as error:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise ContextStorageError("无法安全写入工具结果。") from error

        return ToolResultArtifact(
            tool_name,
            tool_call_id,
            relative_manifest,
            len(content_bytes),
            content_hash,
            _preview(safe_content, self.policy.preview_bytes),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        shutil.rmtree(self.session_dir, ignore_errors=True)

    def _cleanup_stale_sessions(self) -> None:
        cutoff = self._clock() - self.policy.stale_session_seconds
        for directory in self.root.iterdir():
            if not directory.is_dir() or directory.name == self.session_id:
                continue
            try:
                data = json.loads((directory / "session.json").read_text(encoding="utf-8"))
                created_at = float(data["created_at"])
                process_id = int(data["process_id"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            if created_at < cutoff and not self._process_alive(process_id):
                shutil.rmtree(directory, ignore_errors=True)


def _result_content(record: ToolExecutionRecord) -> str:
    return json.dumps(
        {
            "content": record.result.content,
            "metadata": thaw_json(record.result.metadata),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _block_size(block: ToolResultBlock) -> int:
    return len(
        _json_bytes(
            {
                "tool_call_id": block.tool_call_id,
                "content": block.content,
                "is_error": block.is_error,
            }
        )
    )


def _reference_content(artifact: ToolResultArtifact, *, include_preview: bool = True) -> str:
    value: dict[str, object] = {
        "externalized": True,
        "tool_name": artifact.tool_name,
        "tool_call_id": artifact.tool_call_id,
        "manifest_path": artifact.manifest_path,
        "original_bytes": artifact.original_bytes,
        "sha256": artifact.sha256,
    }
    if include_preview:
        value["preview"] = artifact.preview
    return _json_bytes(value).decode("utf-8")


def _is_externalized(content: str) -> bool:
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(value, dict) and value.get("externalized") is True


class ToolResultExternalizer:
    """按单结果和单消息预算控制工具结果。"""

    def __init__(self, store: ContextArtifactStore) -> None:
        self.store = store
        self.policy = store.policy

    def build_result_message(self, records: list[ToolExecutionRecord]) -> ChatMessage:
        ordered = sorted(records, key=lambda record: record.position)
        blocks = tuple(
            ToolResultBlock(
                record.call.id,
                _result_content(record),
                record.result.is_error,
            )
            for record in ordered
        )
        names = {record.call.id: record.call.name for record in ordered}
        return self._normalize_message(ChatMessage("user", blocks), names)

    def normalize_messages(self, messages: tuple[ChatMessage, ...]) -> tuple[ChatMessage, ...]:
        tool_names: dict[str, str] = {}
        normalized: list[ChatMessage] = []
        for message in messages:
            for call in message.blocks(ToolCallBlock):
                tool_names[call.id] = call.name
            if message.blocks(ToolResultBlock):
                message = self._normalize_message(message, tool_names)
            normalized.append(message)
        return tuple(normalized)

    def _normalize_message(
        self,
        message: ChatMessage,
        tool_names: dict[str, str],
    ) -> ChatMessage:
        blocks = list(message.content)
        artifacts: dict[int, ToolResultArtifact] = {}
        sizes: dict[int, int] = {}

        for index, block in enumerate(blocks):
            if not isinstance(block, ToolResultBlock) or _is_externalized(block.content):
                continue
            safe_content = self.store.redactor.redact_text(block.content)
            block = ToolResultBlock(block.tool_call_id, safe_content, block.is_error)
            blocks[index] = block
            sizes[index] = len(safe_content.encode("utf-8"))
            if sizes[index] > self.policy.single_tool_result_bytes:
                artifacts[index] = self._store_block(
                    block,
                    tool_names.get(block.tool_call_id, "unknown_tool"),
                )
                blocks[index] = ToolResultBlock(
                    block.tool_call_id,
                    _reference_content(artifacts[index]),
                    block.is_error,
                )

        def total_size() -> int:
            return sum(_block_size(block) for block in blocks if isinstance(block, ToolResultBlock))

        if total_size() > self.policy.message_tool_results_bytes:
            candidates = sorted(
                (index for index in sizes if index not in artifacts),
                key=lambda index: (-sizes[index], index),
            )
            for index in candidates:
                block = blocks[index]
                if not isinstance(block, ToolResultBlock):
                    continue
                artifacts[index] = self._store_block(
                    block,
                    tool_names.get(block.tool_call_id, "unknown_tool"),
                )
                blocks[index] = ToolResultBlock(
                    block.tool_call_id,
                    _reference_content(artifacts[index]),
                    block.is_error,
                )
                if total_size() <= self.policy.message_tool_results_bytes:
                    break

        if total_size() > self.policy.message_tool_results_bytes:
            for index in sorted(
                artifacts,
                key=lambda item: (-len(artifacts[item].preview.encode("utf-8")), item),
            ):
                block = blocks[index]
                if not isinstance(block, ToolResultBlock):
                    continue
                blocks[index] = ToolResultBlock(
                    block.tool_call_id,
                    _reference_content(artifacts[index], include_preview=False),
                    block.is_error,
                )
                if total_size() <= self.policy.message_tool_results_bytes:
                    break

        return ChatMessage(message.role, tuple(blocks))

    def _store_block(self, block: ToolResultBlock, tool_name: str) -> ToolResultArtifact:
        return self.store.store(
            tool_name,
            block.tool_call_id,
            block.content,
            is_error=block.is_error,
        )
