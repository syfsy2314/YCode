"""受管 Worktree 完整生命周期编排。"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ycode.config.models import WorktreeConfig
from ycode.worktrees.git import (
    GitWorktreeClient,
    LinkedWorktreeHeadReader,
    WorktreeGitError,
    deletion_decision,
)
from ycode.worktrees.initialize import (
    WorktreeInitializationError,
    WorktreeInitializer,
    git_config_environment,
)
from ycode.worktrees.models import (
    WorktreeDeleteDecision,
    WorktreeDisposition,
    WorktreeLease,
    WorktreeLifecycle,
    WorktreeOwner,
    WorktreeRecord,
    WorktreeStatusSnapshot,
    WorktreeSummary,
)
from ycode.worktrees.naming import WorktreeName, managed_worktree_name
from ycode.worktrees.store import RECORD_VERSION, WorktreeStore, WorktreeStoreError


class WorktreeManagerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class WorktreeDeletePreview:
    record: WorktreeRecord
    status: WorktreeStatusSnapshot
    decision: WorktreeDeleteDecision
    force_required: bool


@dataclass(frozen=True, slots=True)
class WorktreeCleanupReport:
    deleted: tuple[str, ...] = ()
    interrupted: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _process_alive(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


class WorktreeManager:
    def __init__(
        self,
        project_root: str | Path,
        config: WorktreeConfig,
        *,
        store: WorktreeStore | None = None,
        git: GitWorktreeClient | None = None,
        initializer: WorktreeInitializer | None = None,
        head_reader: LinkedWorktreeHeadReader | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        process_id: int | None = None,
        process_instance_id: str | None = None,
        process_alive: Callable[[int], bool] = _process_alive,
        name_attempts: int = 8,
    ) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        self.config = config
        self.store = store or WorktreeStore(self.project_root, process_alive=process_alive)
        self.git = git or GitWorktreeClient(self.project_root)
        self.initializer = initializer or WorktreeInitializer(
            self.project_root,
            config,
            self.git,
        )
        self.head_reader = head_reader
        self._clock = clock
        self._process_id = process_id or os.getpid()
        self._process_instance_id = process_instance_id or uuid4().hex
        self._process_alive = process_alive
        self._name_attempts = name_attempts

    async def acquire(self, role: str, session_id: str, task_id: str) -> WorktreeLease:
        owner = self._owner(session_id, task_id)
        try:
            with self.store.mutation(owner):
                base: str | None = None
                for attempt in range(self._name_attempts):
                    name = managed_worktree_name(role, task_id, attempt=attempt)
                    path = self.store.expected_worktree_path(name)
                    record = self.store.get(name)
                    if _path_exists(path):
                        if record is not None:
                            return self._recover(record, owner)
                        continue
                    if record is not None:
                        raise WorktreeManagerError(
                            "record_without_worktree",
                            f"Worktree 管理记录存在但目录缺失：{name.value}",
                        )
                    if base is None:
                        base = await self.git.ensure_repository()
                    if await self.git.branch_exists(name.branch):
                        continue
                    return await self._create(name, path, base, owner)
        except WorktreeManagerError:
            raise
        except WorktreeGitError as error:
            raise WorktreeManagerError(error.code, str(error)) from error
        except WorktreeStoreError as error:
            raise WorktreeManagerError(error.code, str(error)) from error
        raise WorktreeManagerError(
            "worktree_name_conflict",
            "无法为子 Agent 分配无冲突的 Worktree 名称。",
        )

    async def finalize(self, lease: WorktreeLease) -> WorktreeSummary:
        record = lease.record
        try:
            with self.store.mutation(record.owner):
                current = self._require_same_owner(record.name, record.owner)
                try:
                    status = await self._inspect(current)
                except WorktreeManagerError as error:
                    status = WorktreeStatusSnapshot(
                        None,
                        checked_at=self._now(),
                        error=error.code,
                    )
                    retained = replace(
                        current,
                        lifecycle=WorktreeLifecycle.RETAINED,
                        last_activity_at=self._now(),
                        last_status=status,
                    )
                    self.store.save(retained)
                    return self._summary(
                        retained,
                        status,
                        WorktreeDisposition.RETAINED,
                        (str(error),),
                    )
                if not status.has_changes:
                    try:
                        await self._remove(current, force=False)
                    except WorktreeManagerError as error:
                        retained = replace(
                            current,
                            lifecycle=WorktreeLifecycle.RETAINED,
                            current_head=status.head,
                            last_activity_at=self._now(),
                            last_status=status,
                        )
                        self.store.save(retained)
                        return self._summary(
                            retained,
                            status,
                            WorktreeDisposition.RETAINED,
                            (str(error),),
                        )
                    return self._summary(
                        current,
                        status,
                        WorktreeDisposition.CLEANED,
                    )
                retained = replace(
                    current,
                    lifecycle=WorktreeLifecycle.RETAINED,
                    current_head=status.head,
                    last_activity_at=self._now(),
                    last_status=status,
                )
                blocking: tuple[str, ...] = ()
                try:
                    await self.git.unlock(current.path)
                except WorktreeGitError as error:
                    blocking = (str(error),)
                self.store.save(retained)
                return self._summary(
                    retained,
                    status,
                    WorktreeDisposition.RETAINED,
                    blocking,
                )
        except WorktreeStoreError as error:
            raise WorktreeManagerError(error.code, str(error)) from error

    def list_records(self) -> tuple[WorktreeRecord, ...]:
        try:
            return self.store.list_records()
        except WorktreeStoreError as error:
            raise WorktreeManagerError(error.code, str(error)) from error

    def records_for_session(self, session_id: str) -> tuple[WorktreeRecord, ...]:
        return tuple(
            record
            for record in self.list_records()
            if record.owner.session_id == session_id
            and record.lifecycle in {WorktreeLifecycle.RETAINED, WorktreeLifecycle.INTERRUPTED}
        )

    async def status(self, name: str) -> WorktreeRecord:
        parsed = self._parse_name(name)
        owner = self._system_owner(f"status:{parsed.value}")
        try:
            with self.store.mutation(owner):
                record = self._require_record(parsed)
                status = await self._inspect(record)
                updated = replace(
                    record,
                    current_head=status.head,
                    last_activity_at=self._now(),
                    last_status=status,
                )
                self.store.save(updated)
                return updated
        except WorktreeStoreError as error:
            raise WorktreeManagerError(error.code, str(error)) from error

    async def prepare_delete(self, name: str, *, force: bool = False) -> WorktreeDeletePreview:
        parsed = self._parse_name(name)
        owner = self._system_owner(f"delete-preview:{parsed.value}")
        try:
            with self.store.mutation(owner):
                record = self._require_record(parsed)
                status = await self._inspect(record)
                decision = deletion_decision(record.lifecycle, status)
                if force and record.lifecycle is WorktreeLifecycle.ACTIVE:
                    raise WorktreeManagerError("worktree_active", "活动 Worktree 不能强制删除。")
                return WorktreeDeletePreview(
                    record,
                    status,
                    decision,
                    force and not decision.allowed,
                )
        except WorktreeStoreError as error:
            raise WorktreeManagerError(error.code, str(error)) from error

    async def delete(
        self,
        name: str,
        *,
        force: bool = False,
        confirmed: bool = False,
    ) -> None:
        parsed = self._parse_name(name)
        owner = self._system_owner(f"delete:{parsed.value}")
        try:
            with self.store.mutation(owner):
                record = self._require_record(parsed)
                status = await self._inspect(record)
                decision = deletion_decision(record.lifecycle, status)
                if record.lifecycle is WorktreeLifecycle.ACTIVE:
                    raise WorktreeManagerError("worktree_active", "活动 Worktree 不能删除。")
                if force:
                    if not confirmed:
                        raise WorktreeManagerError(
                            "force_confirmation_required", "强制删除需要交互确认。"
                        )
                elif not decision.allowed:
                    raise WorktreeManagerError(
                        decision.reasons[0],
                        "Worktree 存在未提交、未推送或未知状态，拒绝删除。",
                    )
                await self._remove(record, force=force)
        except WorktreeStoreError as error:
            raise WorktreeManagerError(error.code, str(error)) from error

    async def cleanup(self) -> WorktreeCleanupReport:
        records, scan_warnings = self.store.scan_records()
        deleted: list[str] = []
        warnings = list(scan_warnings)
        now = self._now()
        ttl = timedelta(hours=self.config.cleanup_ttl_hours)
        for candidate in records:
            if not candidate.name.startswith("agents/"):
                continue
            if candidate.lifecycle is WorktreeLifecycle.ACTIVE:
                continue
            if now - candidate.last_activity_at < ttl:
                continue
            owner = self._system_owner(f"cleanup:{candidate.name}")
            try:
                with self.store.mutation(owner):
                    record = self._require_record(WorktreeName(candidate.name))
                    if record.lifecycle is WorktreeLifecycle.ACTIVE:
                        continue
                    status = await self._inspect(record)
                    decision = deletion_decision(record.lifecycle, status)
                    if not decision.allowed:
                        warnings.append(f"{record.name}: {', '.join(decision.reasons)}")
                        continue
                    await self._remove(record, force=False)
                    deleted.append(record.name)
            except (WorktreeManagerError, WorktreeStoreError, OSError) as error:
                warnings.append(f"{candidate.name}: {error}")
        return WorktreeCleanupReport(tuple(deleted), (), tuple(warnings))

    async def reconcile_startup(self) -> WorktreeCleanupReport:
        records, scan_warnings = self.store.scan_records()
        interrupted: list[str] = []
        warnings = list(scan_warnings)
        for candidate in records:
            if not candidate.name.startswith("agents/"):
                continue
            if candidate.lifecycle is not WorktreeLifecycle.ACTIVE:
                continue
            if self._process_alive(candidate.owner.process_id):
                continue
            owner = self._system_owner(f"startup:{candidate.name}")
            try:
                with self.store.mutation(owner):
                    record = self._require_record(WorktreeName(candidate.name))
                    if record.lifecycle is not WorktreeLifecycle.ACTIVE:
                        continue
                    self.store.save(replace(record, lifecycle=WorktreeLifecycle.INTERRUPTED))
                    interrupted.append(record.name)
            except (WorktreeManagerError, WorktreeStoreError, OSError) as error:
                warnings.append(f"{candidate.name}: {error}")
        cleanup = await self.cleanup()
        return WorktreeCleanupReport(
            cleanup.deleted,
            tuple(interrupted),
            (*warnings, *cleanup.warnings),
        )

    def ensure_start_allowed(self, start_dir: str | Path) -> None:
        start = Path(start_dir).resolve(strict=True)
        agents_root = self.store.worktrees_root / "agents"
        normalized_parts = tuple(part.casefold() for part in start.parts)
        managed_marker = (".ycode", "worktrees", "agents")
        marker_found = any(
            normalized_parts[index : index + len(managed_marker)] == managed_marker
            for index in range(len(normalized_parts) - len(managed_marker) + 1)
        )
        if marker_found or _is_within(start, agents_root):
            raise WorktreeManagerError(
                "managed_worktree_start",
                "不能从 YCode 受管 Worktree 内启动，请返回主仓库。",
            )

    async def _create(
        self,
        name: WorktreeName,
        path: Path,
        base: str,
        owner: WorktreeOwner,
    ) -> WorktreeLease:
        now = self._now()
        creating = WorktreeRecord(
            RECORD_VERSION,
            name.value,
            str(path),
            name.branch,
            base,
            base,
            WorktreeLifecycle.CREATING,
            now,
            now,
            False,
            (),
            owner,
        )
        self.store.save(creating)
        try:
            await self.git.create(path, name.branch, base, f"{owner.session_id}/{owner.task_id}")
            initialized = await self.initializer.initialize(path)
            active = replace(
                creating,
                lifecycle=WorktreeLifecycle.ACTIVE,
                initialization_complete=True,
                initialization_warnings=initialized.warning_messages,
                hooks_path=str(initialized.hooks_path),
                custom_hooks=initialized.custom_hooks,
                linked_directories=tuple(link.relative_path for link in initialized.links),
            )
            self.store.save(active)
            return WorktreeLease(active, tuple(initialized.git_environment.items()))
        except (WorktreeGitError, WorktreeInitializationError, WorktreeStoreError) as error:
            await self._rollback(creating)
            raise WorktreeManagerError(
                str(getattr(error, "code", "create_failed")), str(error)
            ) from error

    def _recover(self, record: WorktreeRecord, owner: WorktreeOwner) -> WorktreeLease:
        if (
            record.owner.session_id != owner.session_id
            or record.owner.task_id != owner.task_id
            or record.lifecycle is not WorktreeLifecycle.ACTIVE
            or not record.initialization_complete
        ):
            raise WorktreeManagerError(
                "fast_recovery_mismatch", "现有 Worktree 不属于当前会话任务。"
            )
        if record.owner.process_instance_id != self._process_instance_id and self._process_alive(
            record.owner.process_id
        ):
            raise WorktreeManagerError("worktree_active", "现有 Worktree 仍由其他进程占用。")
        try:
            reader = self.head_reader or LinkedWorktreeHeadReader(self.project_root)
            head = reader.read(record.path, record)
            updated = replace(
                record,
                current_head=head.oid,
                owner=owner,
                last_activity_at=self._now(),
            )
            self._validate_recorded_links(updated)
            self.store.save(updated)
        except WorktreeManagerError:
            raise
        except (WorktreeGitError, WorktreeStoreError, WorktreeInitializationError) as error:
            raise WorktreeManagerError(error.code, str(error)) from error
        except (OSError, ValueError) as error:
            raise WorktreeManagerError(
                "fast_recovery_mismatch", "Worktree Git 元数据无法确认。"
            ) from error
        environment = (
            git_config_environment("core.hooksPath", record.hooks_path)
            if record.custom_hooks and record.hooks_path is not None
            else {}
        )
        return WorktreeLease(updated, tuple(environment.items()))

    async def _inspect(self, record: WorktreeRecord) -> WorktreeStatusSnapshot:
        if not _path_exists(Path(record.path)):
            raise WorktreeManagerError("worktree_missing", "受管 Worktree 目录不存在。")
        try:
            entries = await self.git.list_worktrees()
            matches = [
                entry for entry in entries if _path_key(entry.path) == _path_key(Path(record.path))
            ]
            if len(matches) != 1 or matches[0].branch != record.branch:
                raise WorktreeManagerError(
                    "worktree_identity_unknown", "Git Worktree 身份与管理记录不匹配。"
                )
            return await self.git.status(record.path, record.base_head)
        except WorktreeManagerError:
            raise
        except WorktreeGitError as error:
            raise WorktreeManagerError(error.code, str(error)) from error

    async def _remove(self, record: WorktreeRecord, *, force: bool) -> None:
        try:
            await self.git.unlock(record.path)
            await self.git.remove(record.path, force=force)
            await self.git.delete_branch(record.branch)
            self.store.delete(WorktreeName(record.name))
        except WorktreeGitError as error:
            raise WorktreeManagerError(error.code, str(error)) from error

    async def _rollback(self, record: WorktreeRecord) -> None:
        failed = False
        created_match = False
        try:
            entries = await self.git.list_worktrees()
            match = next(
                (
                    entry
                    for entry in entries
                    if _path_key(entry.path) == _path_key(Path(record.path))
                    and entry.branch == record.branch
                ),
                None,
            )
            if match is not None:
                created_match = True
                await self.git.unlock(record.path)
                await self.git.remove(record.path, force=True)
            if created_match and await self.git.branch_exists(record.branch):
                await self.git.delete_branch(record.branch)
        except WorktreeGitError:
            failed = True
        if failed:
            try:
                self.store.save(replace(record, lifecycle=WorktreeLifecycle.INTERRUPTED))
            except WorktreeStoreError:
                pass
            return
        self.store.delete(WorktreeName(record.name))

    def _validate_recorded_links(self, record: WorktreeRecord) -> None:
        worktree = Path(record.path)
        for relative in record.linked_directories:
            parts = PurePosixPath(relative).parts
            source = self.project_root.joinpath(*parts).resolve(strict=True)
            target = worktree.joinpath(*parts)
            if not target.is_dir() or _path_key(target.resolve(strict=True)) != _path_key(source):
                raise WorktreeManagerError(
                    "fast_recovery_mismatch", "Worktree 依赖目录链接与管理记录不匹配。"
                )

    def _require_record(self, name: WorktreeName) -> WorktreeRecord:
        record = self.store.get(name)
        if record is None:
            raise WorktreeManagerError("worktree_not_found", f"Worktree 不存在：{name.value}")
        return record

    def _require_same_owner(self, name: str, owner: WorktreeOwner) -> WorktreeRecord:
        record = self._require_record(WorktreeName(name))
        if record.owner.session_id != owner.session_id or record.owner.task_id != owner.task_id:
            raise WorktreeManagerError("worktree_owner_mismatch", "Worktree owner 不匹配。")
        return record

    def _owner(self, session_id: str, task_id: str) -> WorktreeOwner:
        return WorktreeOwner(
            session_id,
            task_id,
            self._process_id,
            self._process_instance_id,
        )

    def _system_owner(self, task_id: str) -> WorktreeOwner:
        return self._owner("worktree-manager", task_id)

    @staticmethod
    def _parse_name(value: str) -> WorktreeName:
        try:
            name = WorktreeName(value.strip())
        except (AttributeError, ValueError) as error:
            raise WorktreeManagerError("worktree_name_invalid", "Worktree 名称无效。") from error
        if not name.value.startswith("agents/"):
            raise WorktreeManagerError("worktree_name_invalid", "只能管理 agents 命名空间。")
        return name

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _summary(
        record: WorktreeRecord,
        status: WorktreeStatusSnapshot,
        disposition: WorktreeDisposition,
        blocking_reasons: tuple[str, ...] = (),
    ) -> WorktreeSummary:
        return WorktreeSummary(
            record.name,
            record.path,
            record.branch,
            record.base_head,
            status.head,
            disposition,
            status,
            record.initialization_warnings,
            blocking_reasons,
        )


def _path_exists(path: Path) -> bool:
    return path.exists()


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _is_within(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((_path_key(path), _path_key(root)))
    except ValueError:
        return False
    return os.path.normcase(common) == _path_key(root)
