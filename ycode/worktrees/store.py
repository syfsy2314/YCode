"""受管 Worktree 记录与本地短时互斥。"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ycode.worktrees.models import (
    WorktreeCommit,
    WorktreeLifecycle,
    WorktreeOwner,
    WorktreeRecord,
    WorktreeStatusSnapshot,
)
from ycode.worktrees.naming import WorktreeName

RECORD_VERSION = 1


class WorktreeStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _process_alive(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


class WorktreeStore:
    def __init__(
        self,
        project_root: str | Path,
        *,
        process_alive: Callable[[int], bool] = _process_alive,
    ) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        self.worktrees_root = self.project_root / ".ycode" / "worktrees"
        self.state_root = self.worktrees_root / ".state"
        self.records_root = self.state_root / "records"
        self._lock_path = self.state_root / "mutation.lock"
        self._process_alive = process_alive

    def expected_worktree_path(self, name: WorktreeName) -> Path:
        return self.worktrees_root.joinpath(*name.segments).resolve(strict=False)

    def get(self, name: WorktreeName) -> WorktreeRecord | None:
        path = self._record_path(name)
        if not path.is_file():
            return None
        record = self._read_record(path)
        self._validate_identity(record, name, path)
        return record

    def list_records(self) -> tuple[WorktreeRecord, ...]:
        records, warnings = self.scan_records()
        if warnings:
            raise WorktreeStoreError("record_invalid", warnings[0])
        return records

    def scan_records(self) -> tuple[tuple[WorktreeRecord, ...], tuple[str, ...]]:
        """扫描全部记录；单条损坏时保留其余结果供清理流程使用。"""
        if not self.records_root.exists():
            return (), ()
        records: list[WorktreeRecord] = []
        warnings: list[str] = []
        try:
            paths = tuple(self.records_root.rglob("*.json"))
        except OSError as error:
            raise WorktreeStoreError(
                "record_scan_failed", "无法扫描 Worktree 管理记录。"
            ) from error
        for path in paths:
            if path.is_symlink() or not path.is_file():
                warnings.append(f"{path}: Worktree 管理记录类型无效。")
                continue
            try:
                relative = path.relative_to(self.records_root).with_suffix("").as_posix()
                name = WorktreeName(relative)
                record = self._read_record(path)
                self._validate_identity(record, name, path)
            except (ValueError, OSError, WorktreeStoreError) as error:
                warnings.append(f"{path}: {error}")
                continue
            records.append(record)
        return tuple(sorted(records, key=lambda item: item.name)), tuple(warnings)

    def save(self, record: WorktreeRecord) -> None:
        try:
            name = WorktreeName(record.name)
        except ValueError as error:
            raise WorktreeStoreError("record_invalid", "Worktree 管理记录名称无效。") from error
        path = self._record_path(name)
        self._validate_identity(record, name, path)
        payload = json.dumps(
            _encode_record(record),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise WorktreeStoreError(
                "record_write_failed", "Worktree 管理记录写入失败。"
            ) from error

    def delete(self, name: WorktreeName) -> None:
        try:
            self._record_path(name).unlink()
        except FileNotFoundError:
            return
        except OSError as error:
            raise WorktreeStoreError(
                "record_delete_failed", "Worktree 管理记录删除失败。"
            ) from error

    @contextmanager
    def mutation(self, owner: WorktreeOwner) -> Iterator[None]:
        self._acquire_lock(owner)
        try:
            yield
        finally:
            try:
                self._lock_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                raise WorktreeStoreError(
                    "lock_release_failed", "Worktree 管理锁释放失败。"
                ) from error

    def _acquire_lock(self, owner: WorktreeOwner) -> None:
        payload = json.dumps(
            {
                "process_id": owner.process_id,
                "process_instance_id": owner.process_instance_id,
                "session_id": owner.session_id,
                "task_id": owner.task_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            self.state_root.mkdir(parents=True, exist_ok=True)
            self._create_lock(payload)
            return
        except FileExistsError:
            pass
        existing_pid = self._read_lock_pid()
        if self._process_alive(existing_pid):
            raise WorktreeStoreError("lock_busy", "Worktree 管理操作正被其他进程占用。")
        try:
            self._lock_path.unlink()
            self._create_lock(payload)
        except FileExistsError as error:
            raise WorktreeStoreError("lock_busy", "Worktree 管理操作正被其他进程占用。") from error
        except OSError as error:
            raise WorktreeStoreError("lock_failed", "Worktree 管理锁创建失败。") from error

    def _create_lock(self, payload: str) -> None:
        with self._lock_path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()

    def _read_lock_pid(self) -> int:
        try:
            data = json.loads(self._lock_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise WorktreeStoreError("lock_unknown", "Worktree 管理锁状态无法确认。") from error
        if not isinstance(data, dict) or type(data.get("process_id")) is not int:
            raise WorktreeStoreError("lock_unknown", "Worktree 管理锁状态无法确认。")
        return data["process_id"]

    def _read_record(self, path: Path) -> WorktreeRecord:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return _decode_record(raw)
        except WorktreeStoreError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise WorktreeStoreError("record_invalid", "Worktree 管理记录损坏。") from error

    def _record_path(self, name: WorktreeName) -> Path:
        return self.records_root.joinpath(*name.segments).with_suffix(".json")

    def _validate_identity(self, record: WorktreeRecord, name: WorktreeName, path: Path) -> None:
        expected_path = self.expected_worktree_path(name)
        record_path = Path(record.path).resolve(strict=False)
        if (
            record.version != RECORD_VERSION
            or record.name != name.value
            or record.branch != name.branch
            or os.path.normcase(str(record_path)) != os.path.normcase(str(expected_path))
            or path != self._record_path(name)
        ):
            raise WorktreeStoreError("record_identity_mismatch", "Worktree 管理记录身份不匹配。")


def _encode_record(record: WorktreeRecord) -> dict[str, object]:
    return {
        "version": record.version,
        "name": record.name,
        "path": record.path,
        "branch": record.branch,
        "base_head": record.base_head,
        "current_head": record.current_head,
        "lifecycle": record.lifecycle.value,
        "created_at": record.created_at.isoformat(),
        "last_activity_at": record.last_activity_at.isoformat(),
        "initialization_complete": record.initialization_complete,
        "initialization_warnings": list(record.initialization_warnings),
        "owner": asdict(record.owner),
        "last_status": (
            _encode_status(record.last_status) if record.last_status is not None else None
        ),
        "hooks_path": record.hooks_path,
        "custom_hooks": record.custom_hooks,
        "linked_directories": list(record.linked_directories),
    }


def _encode_status(status: WorktreeStatusSnapshot) -> dict[str, object]:
    return {
        "head": status.head,
        "staged": list(status.staged),
        "modified": list(status.modified),
        "untracked": list(status.untracked),
        "commits": [asdict(commit) for commit in status.commits],
        "diff_stat": status.diff_stat,
        "upstream": status.upstream,
        "unpushed_commits": [asdict(commit) for commit in status.unpushed_commits],
        "checked_at": status.checked_at.isoformat() if status.checked_at is not None else None,
        "error": status.error,
    }


def _decode_record(raw: object) -> WorktreeRecord:
    data = _strict_mapping(
        raw,
        {
            "version",
            "name",
            "path",
            "branch",
            "base_head",
            "current_head",
            "lifecycle",
            "created_at",
            "last_activity_at",
            "initialization_complete",
            "initialization_warnings",
            "owner",
            "last_status",
            "hooks_path",
            "custom_hooks",
            "linked_directories",
        },
    )
    owner_data = _strict_mapping(
        data["owner"],
        {"session_id", "task_id", "process_id", "process_instance_id"},
    )
    return WorktreeRecord(
        _exact_int(data["version"]),
        _string(data["name"]),
        _string(data["path"]),
        _string(data["branch"]),
        _string(data["base_head"]),
        _optional_string(data["current_head"]),
        WorktreeLifecycle(_string(data["lifecycle"])),
        _datetime(data["created_at"]),
        _datetime(data["last_activity_at"]),
        _boolean(data["initialization_complete"]),
        _string_tuple(data["initialization_warnings"]),
        WorktreeOwner(
            _string(owner_data["session_id"]),
            _string(owner_data["task_id"]),
            _exact_int(owner_data["process_id"]),
            _string(owner_data["process_instance_id"]),
        ),
        _decode_status(data["last_status"]),
        _optional_string(data["hooks_path"]),
        _boolean(data["custom_hooks"]),
        _string_tuple(data["linked_directories"]),
    )


def _decode_status(raw: object) -> WorktreeStatusSnapshot | None:
    if raw is None:
        return None
    data = _strict_mapping(
        raw,
        {
            "head",
            "staged",
            "modified",
            "untracked",
            "commits",
            "diff_stat",
            "upstream",
            "unpushed_commits",
            "checked_at",
            "error",
        },
    )
    return WorktreeStatusSnapshot(
        _optional_string(data["head"]),
        _string_tuple(data["staged"]),
        _string_tuple(data["modified"]),
        _string_tuple(data["untracked"]),
        _commit_tuple(data["commits"]),
        _string(data["diff_stat"], allow_empty=True),
        _optional_string(data["upstream"]),
        _commit_tuple(data["unpushed_commits"]),
        _optional_datetime(data["checked_at"]),
        _optional_string(data["error"]),
    )


def _strict_mapping(raw: object, fields: set[str]) -> Mapping[str, object]:
    if (
        not isinstance(raw, dict)
        or set(raw) != fields
        or any(not isinstance(key, str) for key in raw)
    ):
        raise ValueError("字段集合无效")
    return raw


def _string(raw: object, *, allow_empty: bool = False) -> str:
    if not isinstance(raw, str) or (not allow_empty and not raw):
        raise ValueError("字符串字段无效")
    return raw


def _optional_string(raw: object) -> str | None:
    return None if raw is None else _string(raw)


def _exact_int(raw: object) -> int:
    if type(raw) is not int:
        raise ValueError("整数字段无效")
    return raw


def _boolean(raw: object) -> bool:
    if type(raw) is not bool:
        raise ValueError("布尔字段无效")
    return raw


def _datetime(raw: object) -> datetime:
    value = datetime.fromisoformat(_string(raw))
    if value.utcoffset() is None:
        raise ValueError("时间字段缺少时区")
    return value


def _optional_datetime(raw: object) -> datetime | None:
    return None if raw is None else _datetime(raw)


def _string_tuple(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ValueError("字符串列表无效")
    return tuple(_string(item) for item in raw)


def _commit_tuple(raw: object) -> tuple[WorktreeCommit, ...]:
    if not isinstance(raw, list):
        raise ValueError("commit 列表无效")
    commits: list[WorktreeCommit] = []
    for item in raw:
        data = _strict_mapping(item, {"oid", "subject"})
        commits.append(WorktreeCommit(_string(data["oid"]), _string(data["subject"])))
    return tuple(commits)
