import httpx

from ycode.hooks.executors import HookActionExecutors
from ycode.hooks.models import HookEvent, HookEventName, HookRule


def _event(name: HookEventName = HookEventName.SESSION_START) -> HookEvent:
    return HookEvent(name, {"event": {"name": name.value}, "value": "demo"})


async def test_agent_and_reminder(tmp_path) -> None:
    executors = HookActionExecutors(tmp_path)
    agent = HookRule.model_validate(
        {"id": "agent-demo", "event": "session.start", "action": {"type": "agent"}}
    )
    result = await executors.execute(agent, _event())
    assert "尚未实现" in result.message

    reminder = HookRule.model_validate(
        {
            "id": "remind",
            "event": "message.before_send",
            "action": {"type": "reminder", "content": "value={{ value }} <safe>"},
        }
    )
    result = await executors.execute(reminder, _event(HookEventName.MESSAGE_BEFORE_SEND))
    assert "value=demo &lt;safe&gt;" in result.reminder
    await executors.close()


async def test_shell_permission_output(tmp_path) -> None:
    executors = HookActionExecutors(tmp_path)
    command = (
        '"' + str((tmp_path / "noop").resolve()) + '"'
        if False
        else 'echo {"permissionDecision":"deny","permissionDecisionReason":"blocked"}'
    )
    rule = HookRule.model_validate(
        {
            "id": "shell",
            "event": "tool.before_execute",
            "action": {"type": "shell", "command": command},
        }
    )
    result = await executors.execute(rule, _event(HookEventName.TOOL_BEFORE_EXECUTE))
    assert result.permission is not None
    assert result.permission.value == "deny"
    await executors.close()


async def test_http_templates(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/demo"
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    executors = HookActionExecutors(tmp_path, client)
    rule = HookRule.model_validate(
        {
            "id": "http",
            "event": "session.start",
            "action": {"type": "http", "method": "POST", "url": "https://x/{{ value }}"},
        }
    )
    result = await executors.execute(rule, _event())
    assert result.status.value == "succeeded"
    await client.aclose()
