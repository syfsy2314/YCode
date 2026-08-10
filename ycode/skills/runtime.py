"""Skill 激活、调用分支和工具策略运行时。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ycode.agent.contracts import AgentMode
from ycode.prompt.runtime import PromptRuntimeContext
from ycode.skills.catalog import SkillCatalog
from ycode.skills.models import (
    SkillCallFrame,
    SkillCallResult,
    SkillCatalogState,
    SkillExecutionMode,
    SkillInvocationSource,
    SkillSnapshot,
    SkillTaskScope,
)


class SkillStateStore(Protocol):
    @property
    def active_session_id(self) -> str | None: ...

    async def append_skill_state(self, active_skill_names: Sequence[str]): ...


class IsolatedSkillExecutor(Protocol):
    async def run(
        self,
        snapshot: SkillSnapshot,
        scope: SkillTaskScope,
        arguments: str | None,
    ) -> str: ...


class SkillRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SkillRuntime:
    def __init__(
        self,
        catalog: SkillCatalog,
        prompt_runtime: PromptRuntimeContext,
        state_store: SkillStateStore | None = None,
        isolated_executor: IsolatedSkillExecutor | None = None,
    ) -> None:
        self._catalog = catalog
        self._prompt_runtime = prompt_runtime
        self._state_store = state_store
        self._isolated_executor = isolated_executor
        self._active_shared: dict[str, SkillSnapshot] = {}
        self.refresh_catalog_prompt()

    def set_isolated_executor(self, executor: IsolatedSkillExecutor) -> None:
        self._isolated_executor = executor

    @property
    def active_shared(self) -> tuple[SkillSnapshot, ...]:
        return tuple(self._active_shared[name] for name in sorted(self._active_shared))

    @property
    def active_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._active_shared))

    @property
    def catalog_entries(self):
        return self._catalog.entries

    def scan_catalog_candidate(self) -> SkillCatalogState:
        return self._catalog.scan_candidate()

    def refresh_catalog_prompt(self) -> None:
        self._prompt_runtime.set_skill_catalog(
            tuple(
                (entry.snapshot.name, entry.snapshot.description)
                for entry in self._catalog.entries
                if entry.snapshot is not None
            )
        )

    def commit_catalog(self, candidate: SkillCatalogState) -> tuple[str, ...]:
        self._catalog.commit(candidate)
        removed = tuple(name for name in self._active_shared if name not in candidate.available)
        for name in removed:
            self._active_shared.pop(name, None)
        self.refresh_catalog_prompt()
        self._refresh_instruction_prompt()
        return removed

    def begin_task(self, mode: AgentMode) -> SkillTaskScope:
        return SkillTaskScope(mode, active_before_turn=dict(self._active_shared))

    def branch_for_isolated(self, parent: SkillTaskScope) -> SkillTaskScope:
        return SkillTaskScope(
            parent.mode,
            active_before_turn={},
            call_stack=list(parent.call_stack),
            authorization=parent.authorization,
            main_branch=False,
        )

    def load_current(self, name: str) -> SkillSnapshot:
        try:
            entry = self._catalog.reload_one(name)
        except KeyError as error:
            raise SkillRuntimeError("skill_not_found", f"Skill 不存在：{name}") from error
        if entry.snapshot is None:
            message = entry.problems[0].message if entry.problems else "Skill 当前不可用"
            raise SkillRuntimeError("skill_unavailable", message)
        return entry.snapshot

    async def invoke(
        self,
        name: str,
        arguments: str | None,
        source: SkillInvocationSource,
        scope: SkillTaskScope,
    ) -> SkillCallResult:
        snapshot = self.load_current(name)
        if self.needs_activation_approval(snapshot, source):
            approved = scope.authorization.approved_skill_fingerprints.get(snapshot.name)
            if approved != snapshot.fingerprint:
                raise SkillRuntimeError(
                    "skill_approval_required",
                    f"Skill {snapshot.name} 的工具预授权需要确认。",
                )
        self.enter_call(scope, snapshot)
        try:
            self.grant_preapproval(scope, snapshot)
            if snapshot.config.execution_mode is SkillExecutionMode.SHARED:
                activated = self.activate_shared(scope, snapshot)
                return SkillCallResult(snapshot.name, SkillExecutionMode.SHARED, activated)
            if self._isolated_executor is None:
                raise SkillRuntimeError("isolated_unavailable", "隔离 Skill 运行器不可用")
            branch = self.branch_for_isolated(scope)
            try:
                handoff = await self._isolated_executor.run(snapshot, branch, arguments)
            except SkillRuntimeError:
                raise
            except Exception as error:
                code = getattr(error, "code", "isolated_failed")
                raise SkillRuntimeError(code, str(error)) from error
            finally:
                self.discard_task(branch)
            return SkillCallResult(
                snapshot.name,
                SkillExecutionMode.ISOLATED,
                False,
                handoff,
            )
        finally:
            self.leave_call(scope, snapshot)

    def enter_call(self, scope: SkillTaskScope, snapshot: SkillSnapshot) -> None:
        names = [frame.snapshot.name for frame in scope.call_stack]
        if snapshot.name in names:
            raise SkillRuntimeError("skill_cycle", f"Skill 循环调用：{snapshot.name}")
        if len(scope.call_stack) >= 3:
            raise SkillRuntimeError("skill_depth", "Skill 最大嵌套深度为 3")
        scope.call_stack.append(SkillCallFrame(snapshot, snapshot.config.visible_tools))

    def leave_call(self, scope: SkillTaskScope, snapshot: SkillSnapshot) -> None:
        if not scope.call_stack or scope.call_stack[-1].snapshot is not snapshot:
            raise RuntimeError("Skill 调用栈状态不一致")
        scope.call_stack.pop()

    def activate_shared(self, scope: SkillTaskScope, snapshot: SkillSnapshot) -> bool:
        previous = scope.pending_shared.get(snapshot.name) or scope.active_before_turn.get(
            snapshot.name
        )
        scope.pending_shared[snapshot.name] = snapshot
        return previous is None or previous.fingerprint != snapshot.fingerprint

    def needs_activation_approval(
        self,
        snapshot: SkillSnapshot,
        source: SkillInvocationSource,
    ) -> bool:
        return bool(snapshot.config.allowed_tools) and source is not SkillInvocationSource.EXPLICIT

    def grant_preapproval(self, scope: SkillTaskScope, snapshot: SkillSnapshot) -> None:
        scope.preapproved_tools.update(snapshot.config.allowed_tools)

    def approve_activation(self, scope: SkillTaskScope, snapshot: SkillSnapshot) -> None:
        scope.authorization.approved_skill_fingerprints[snapshot.name] = snapshot.fingerprint
        self.grant_preapproval(scope, snapshot)

    def visible_tools(
        self,
        scope: SkillTaskScope,
        base_tools: frozenset[str],
    ) -> frozenset[str]:
        if not scope.main_branch:
            if not scope.call_stack or scope.call_stack[-1].visible_tools is None:
                return base_tools
            return base_tools & scope.call_stack[-1].visible_tools

        snapshots = {
            **dict(scope.active_before_turn),
            **scope.pending_shared,
        }.values()
        snapshots = tuple(snapshots)
        if not snapshots or any(item.config.visible_tools is None for item in snapshots):
            return base_tools
        visible = frozenset().union(
            *(item.config.visible_tools or frozenset() for item in snapshots)
        )
        return base_tools & visible

    def candidate_active_names(self, scope: SkillTaskScope) -> tuple[str, ...]:
        if not scope.main_branch:
            return self.active_names
        return tuple(sorted({*scope.active_before_turn, *scope.pending_shared}))

    def refresh_task_prompt(self, scope: SkillTaskScope) -> None:
        if not scope.main_branch:
            return
        snapshots = {
            **dict(scope.active_before_turn),
            **scope.pending_shared,
        }
        self._prompt_runtime.set_skill_instructions(
            tuple((item.name, item.instructions) for item in snapshots.values())
        )

    def commit_task(self, scope: SkillTaskScope) -> None:
        if scope.main_branch:
            self._active_shared = {
                **dict(scope.active_before_turn),
                **scope.pending_shared,
            }
            self._refresh_instruction_prompt()
        self._finish_scope(scope)

    def discard_task(self, scope: SkillTaskScope) -> None:
        self._finish_scope(scope)
        if scope.main_branch:
            self._refresh_instruction_prompt()

    async def deactivate(self, name: str) -> bool:
        if name not in self._active_shared:
            return False
        candidate = tuple(item for item in self.active_names if item != name)
        if self._state_store is not None and self._state_store.active_session_id is not None:
            await self._state_store.append_skill_state(candidate)
        self._active_shared.pop(name)
        self._refresh_instruction_prompt()
        return True

    async def restore(self, names: Sequence[str]) -> tuple[str, ...]:
        candidate: dict[str, SkillSnapshot] = {}
        warnings: list[str] = []
        for name in sorted(set(names)):
            try:
                snapshot = self.load_current(name)
            except SkillRuntimeError as error:
                warnings.append(f"无法恢复 Skill {name}：{error}")
                continue
            if snapshot.config.execution_mode.value != "shared":
                warnings.append(f"无法恢复隔离 Skill：{name}")
                continue
            candidate[name] = snapshot
        self._active_shared = candidate
        self._refresh_instruction_prompt()
        return tuple(warnings)

    def clear(self) -> None:
        self._active_shared.clear()
        self._refresh_instruction_prompt()

    def _refresh_instruction_prompt(self) -> None:
        self._prompt_runtime.set_skill_instructions(
            tuple((item.name, item.instructions) for item in self.active_shared)
        )

    @staticmethod
    def _finish_scope(scope: SkillTaskScope) -> None:
        scope.pending_shared.clear()
        scope.call_stack.clear()
        if scope.main_branch:
            scope.authorization.clear()


__all__ = ["SkillRuntime", "SkillRuntimeError"]
