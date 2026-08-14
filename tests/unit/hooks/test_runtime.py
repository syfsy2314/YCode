import asyncio

from ycode.hooks.models import HookEvent, HookEventName, HookRule
from ycode.hooks.runtime import HookRuntime


def _event(name: HookEventName = HookEventName.SESSION_START) -> HookEvent:
    return HookEvent(name, {"event": {"name": name.value}})


async def test_enabled_and_once(tmp_path) -> None:
    rules = (
        HookRule.model_validate(
            {
                "id": "disabled",
                "enabled": False,
                "event": "session.start",
                "action": {"type": "agent"},
            }
        ),
        HookRule.model_validate(
            {
                "id": "once",
                "once": True,
                "event": "session.start",
                "action": {"type": "agent"},
            }
        ),
    )
    runtime = HookRuntime(rules, tmp_path)
    first = await runtime.dispatch(_event())
    second = await runtime.dispatch(_event())
    assert len(first.notices) == 1
    assert second.notices == ()
    assert runtime.rules[0].executed is False
    assert runtime.rules[1].executed is True
    await runtime.close()


async def test_permission_priority_and_deny_short_circuit(tmp_path) -> None:
    rules = tuple(
        HookRule.model_validate(
            {
                "id": name,
                "event": "tool.before_execute",
                "permission": permission,
                "action": {"type": "agent"},
            }
        )
        for name, permission in (("allow", "allow"), ("ask", "ask"), ("deny", "deny"))
    )
    runtime = HookRuntime(rules, tmp_path)
    result = await runtime.dispatch(_event(HookEventName.TOOL_BEFORE_EXECUTE))
    assert result.permission is not None
    assert result.permission.value == "deny"
    await runtime.close()


async def test_async_action_does_not_block(tmp_path) -> None:
    rule = HookRule.model_validate(
        {
            "id": "background",
            "event": "session.start",
            "async": True,
            "action": {"type": "shell", "command": "echo ok"},
        }
    )
    runtime = HookRuntime((rule,), tmp_path)
    await asyncio.wait_for(runtime.dispatch(_event()), timeout=1)
    assert runtime.rules[0].executed is True
    await runtime.close()
