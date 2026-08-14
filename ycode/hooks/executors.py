"""Hook 动作执行器。"""

import asyncio
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from ycode.hooks.logging import bounded_summary
from ycode.hooks.models import (
    AgentHookAction,
    HookActionResult,
    HookActionStatus,
    HookEvent,
    HookEventName,
    HookRule,
    HttpHookAction,
    ReminderHookAction,
    ShellHookAction,
    ShellPermissionOutput,
)
from ycode.hooks.template import escape_reminder_text, render_hook_template


class HookActionExecutors:
    def __init__(
        self,
        project: Path,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._project = project.resolve()
        self._client = http_client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = http_client is None

    async def execute(self, rule: HookRule, event: HookEvent) -> HookActionResult:
        try:
            async with asyncio.timeout(rule.timeout_seconds):
                if isinstance(rule.action, ShellHookAction):
                    return await self._shell(rule, event)
                if isinstance(rule.action, HttpHookAction):
                    return await self._http(rule, event)
                if isinstance(rule.action, ReminderHookAction):
                    return self._reminder(rule, event)
                if isinstance(rule.action, AgentHookAction):
                    return HookActionResult(
                        HookActionStatus.SUCCEEDED,
                        message=f"子 Agent Hook 尚未实现：{rule.id}",
                    )
                raise TypeError("未知 Hook 动作")
        except TimeoutError:
            return HookActionResult(HookActionStatus.TIMED_OUT, message="Hook 动作执行超时")
        except asyncio.CancelledError:
            return HookActionResult(HookActionStatus.CANCELLED, message="Hook 动作已取消")
        except Exception as error:
            return HookActionResult(
                HookActionStatus.FAILED,
                message=bounded_summary(error),
            )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _shell(self, rule: HookRule, event: HookEvent) -> HookActionResult:
        assert isinstance(rule.action, ShellHookAction)
        command = render_hook_template(rule.action.command, event.context)
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self._project,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await process.communicate()
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        stdout = stdout_bytes.decode(errors="replace").strip()
        stderr = stderr_bytes.decode(errors="replace").strip()
        if process.returncode != 0:
            return HookActionResult(
                HookActionStatus.FAILED,
                message=bounded_summary(stderr or stdout or f"exit {process.returncode}"),
            )
        permission = None
        reason = ""
        if event.name is HookEventName.TOOL_BEFORE_EXECUTE and not rule.async_ and stdout:
            try:
                parsed = ShellPermissionOutput.model_validate_json(stdout)
            except (ValidationError, ValueError):
                return HookActionResult(
                    HookActionStatus.FAILED,
                    message="Shell Hook 权限输出不是合法 JSON",
                )
            permission = parsed.permissionDecision
            reason = parsed.permissionDecisionReason
        return HookActionResult(
            HookActionStatus.SUCCEEDED,
            permission=permission,
            reason=reason,
            message=bounded_summary(stderr or stdout),
        )

    async def _http(self, rule: HookRule, event: HookEvent) -> HookActionResult:
        assert isinstance(rule.action, HttpHookAction)
        action = rule.action
        kwargs: dict[str, Any] = {
            "headers": {
                key: render_hook_template(value, event.context)
                for key, value in action.headers.items()
            }
        }
        if action.body is not None:
            kwargs["content"] = render_hook_template(action.body, event.context)
        if "json_" in action.model_fields_set:
            kwargs["json"] = _render_json_templates(action.json_, event.context)
        response = await self._client.request(
            action.method.value,
            render_hook_template(action.url, event.context),
            **kwargs,
        )
        if 200 <= response.status_code < 300:
            return HookActionResult(
                HookActionStatus.SUCCEEDED,
                message=bounded_summary(response.text),
            )
        return HookActionResult(
            HookActionStatus.FAILED,
            message=f"HTTP {response.status_code}: {bounded_summary(response.text)}",
        )

    @staticmethod
    def _reminder(rule: HookRule, event: HookEvent) -> HookActionResult:
        assert isinstance(rule.action, ReminderHookAction)
        content = escape_reminder_text(render_hook_template(rule.action.content, event.context))
        reminder = f"Hook rule: {rule.id}\nHook event: {event.name.value}\n\n{content}"
        return HookActionResult(HookActionStatus.SUCCEEDED, reminder=reminder)


def _render_json_templates(value: object, context: object) -> object:
    if isinstance(value, str):
        return render_hook_template(value, context)
    if isinstance(value, list):
        return [_render_json_templates(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_json_templates(item, context) for key, item in value.items()}
    return value
