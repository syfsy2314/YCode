"""Hook 规则运行时。"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx

from ycode.hooks.executors import HookActionExecutors
from ycode.hooks.logging import log_hook_result
from ycode.hooks.matching import matches_hook_conditions
from ycode.hooks.models import (
    HookActionResult,
    HookActionStatus,
    HookDispatchResult,
    HookEvent,
    HookEventName,
    HookPermissionDecision,
    HookRule,
)
from ycode.prompt.models import SupplementKind, SystemSupplement


@dataclass(slots=True)
class RuntimeHookRule:
    config: HookRule
    executed: bool = False


class HookRuntime:
    def __init__(
        self,
        rules: tuple[HookRule, ...],
        project: Path,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._rules = [RuntimeHookRule(rule) for rule in rules]
        self._executors = HookActionExecutors(project, http_client)
        self._reminders: dict[str, list[SystemSupplement]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False

    @property
    def rules(self) -> tuple[RuntimeHookRule, ...]:
        return tuple(self._rules)

    async def dispatch(
        self,
        event: HookEvent,
        *,
        scope_id: str = "main",
    ) -> HookDispatchResult:
        if not scope_id:
            raise ValueError("Hook scope ID 不能为空")
        if self._closing and event.name is not HookEventName.SESSION_END:
            return HookDispatchResult()
        aggregate: HookPermissionDecision | None = None
        reason = ""
        notices: list[str] = []
        for runtime_rule in self._rules:
            rule = runtime_rule.config
            if rule.event is not event.name or not rule.enabled:
                continue
            if rule.once and runtime_rule.executed:
                continue
            try:
                if not matches_hook_conditions(rule.conditions, event.context):
                    continue
            except Exception as error:
                log_hook_result(event.name.value, rule.id, rule.action.type, "failed", str(error))
                continue
            runtime_rule.executed = True
            if rule.async_:
                task = asyncio.create_task(self._run_background(runtime_rule, event))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                continue
            result = await self._execute(runtime_rule, event)
            if result.reminder:
                self._reminders.setdefault(scope_id, []).append(
                    SystemSupplement(SupplementKind.SYSTEM_REMINDER, result.reminder)
                )
            if result.message and rule.action.type == "agent":
                notices.append(result.message)
            decision = result.permission or rule.permission
            if event.name is not HookEventName.TOOL_BEFORE_EXECUTE or decision is None:
                continue
            decision_reason = result.reason or f"Hook 规则 {rule.id}"
            if decision is HookPermissionDecision.DENY:
                return HookDispatchResult(decision, decision_reason, tuple(notices))
            if decision is HookPermissionDecision.ASK:
                aggregate = decision
                reason = decision_reason
            elif aggregate is None:
                aggregate = decision
                reason = decision_reason
        return HookDispatchResult(aggregate, reason, tuple(notices))

    def take_reminders(self, scope_id: str = "main") -> tuple[SystemSupplement, ...]:
        reminders = tuple(self._reminders.pop(scope_id, ()))
        return reminders

    def clear_scope(self, scope_id: str) -> None:
        self._reminders.pop(scope_id, None)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._tasks:
            _done, pending = await asyncio.wait(self._tasks, timeout=3)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        await self._executors.close()

    async def _execute(
        self,
        runtime_rule: RuntimeHookRule,
        event: HookEvent,
    ) -> HookActionResult:
        rule = runtime_rule.config
        try:
            result = await self._executors.execute(rule, event)
        except Exception as error:
            log_hook_result(event.name.value, rule.id, rule.action.type, "failed", str(error))
            return HookActionResult(HookActionStatus.FAILED, message=str(error))
        log_hook_result(
            event.name.value,
            rule.id,
            rule.action.type,
            result.status.value,
            result.message,
        )
        return result

    async def _run_background(
        self,
        runtime_rule: RuntimeHookRule,
        event: HookEvent,
    ) -> None:
        await self._execute(runtime_rule, event)
