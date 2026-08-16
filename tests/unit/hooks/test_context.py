from ycode.core.messages import ChatMessage, ToolCallBlock
from ycode.hooks.context import HookContextFactory
from ycode.hooks.models import HookEventName


def test_message_and_tool_context(tmp_path) -> None:
    factory = HookContextFactory(tmp_path, "session-1")
    message = factory.message(HookEventName.TURN_START, "turn-1", ChatMessage.user_text("hi"))
    assert message.context["message"]["content"] == "hi"  # type: ignore[index]

    call = ToolCallBlock("call-1", "write_file", {"path": "raw"})
    event = factory.tool_before("turn-1", call, {"path": "src/app.py"})
    assert event.context["file"]["path"] == "src/app.py"  # type: ignore[index]


def test_subagent_metadata_is_added_to_every_context(tmp_path) -> None:
    factory = HookContextFactory(
        tmp_path,
        "session-1",
        task_metadata={
            "task_id": "task-1",
            "creation_mode": "fork",
            "role": None,
            "run_mode": "async",
        },
    )

    event = factory.simple(HookEventName.AGENT_ERROR, error={"message": "failed"})

    assert event.context["subagent"] == {
        "task_id": "task-1",
        "creation_mode": "fork",
        "role": None,
        "run_mode": "async",
    }
