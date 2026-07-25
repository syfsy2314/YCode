import pytest

from ycode.core import (
    RedactedThinkingBlock,
    StopReason,
    StreamEnd,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallBlock,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    thaw_json,
)
from ycode.errors import MessageAssemblyError
from ycode.session.assembler import ResponseAssembler


def consume(assembler: ResponseAssembler, *events: object) -> None:
    for event in events:
        assembler.consume(event)  # type: ignore[arg-type]


def test_assembles_interleaved_blocks_in_index_order() -> None:
    assembler = ResponseAssembler()
    tool = ToolCallBlock("call-1", "read", {"path": "a.py"})
    consume(
        assembler,
        ToolCallStart(2, "call-1", "read"),
        TextDelta(1, "ans"),
        ToolCallDelta(2, '{"path":'),
        TextDelta(1, "wer"),
        ToolCallDelta(2, '"a.py"}'),
        ToolCallComplete(2, tool),
        StreamEnd(StopReason.TOOL_USE, "tool_use"),
    )

    message = assembler.finish()
    assert message.content[0] == TextBlock("answer")
    result = message.content[1]
    assert isinstance(result, ToolCallBlock)
    assert (result.id, result.name, thaw_json(result.arguments)) == (
        "call-1",
        "read",
        {"path": "a.py"},
    )
    assert assembler.stop_reason is StopReason.TOOL_USE
    assert assembler.provider_reason == "tool_use"


def test_assembles_thinking_and_redacted_blocks() -> None:
    assembler = ResponseAssembler()
    thinking = ThinkingBlock("reason", "sig")
    redacted = RedactedThinkingBlock("encrypted-data")
    consume(
        assembler,
        ThinkingDelta(0, "rea"),
        ThinkingDelta(0, "son"),
        ThinkingComplete(0, thinking),
        ThinkingComplete(1, redacted),
        StreamEnd(StopReason.END_TURN),
    )

    assert assembler.finish().content == (thinking, redacted)


def test_accepts_empty_tool_arguments() -> None:
    assembler = ResponseAssembler()
    block = ToolCallBlock("call-1", "read", {})
    consume(
        assembler,
        ToolCallStart(0, "call-1", "read"),
        ToolCallComplete(0, block),
        StreamEnd(StopReason.TOOL_USE),
    )

    assert assembler.finish().content == (block,)


@pytest.mark.parametrize(
    "events, message",
    [
        ([ToolCallDelta(0, "{}")], "缺少开始"),
        (
            [ToolCallStart(0, "call-1", "read"), ToolCallStart(0, "call-1", "read")],
            "重复开始",
        ),
        ([ThinkingDelta(0, "reason"), TextDelta(0, "bad")], "类型不匹配"),
        ([ThinkingComplete(0, ThinkingBlock("reason", "sig"))], "缺少"),
        (
            [
                ThinkingDelta(0, "reason"),
                ThinkingComplete(0, ThinkingBlock("different", "sig")),
            ],
            "不一致",
        ),
        (
            [
                ThinkingDelta(0, "reason"),
                StreamEnd(StopReason.END_TURN),
            ],
            "尚未完成",
        ),
        (
            [
                ToolCallStart(0, "call-1", "read"),
                StreamEnd(StopReason.TOOL_USE),
            ],
            "尚未完成",
        ),
        ([StreamEnd(StopReason.END_TURN)], "没有内容块"),
    ],
)
def test_rejects_invalid_lifecycle(events: list[object], message: str) -> None:
    assembler = ResponseAssembler()
    with pytest.raises(MessageAssemblyError, match=message):
        consume(assembler, *events)


@pytest.mark.parametrize("partial_json", ["{bad", "[]", "1"])
def test_rejects_invalid_or_non_object_tool_json(partial_json: str) -> None:
    assembler = ResponseAssembler()
    consume(
        assembler,
        ToolCallStart(0, "call-1", "read"),
        ToolCallDelta(0, partial_json),
    )

    with pytest.raises(MessageAssemblyError, match="JSON"):
        assembler.consume(ToolCallComplete(0, ToolCallBlock("call-1", "read", {})))


def test_rejects_inconsistent_tool_completion() -> None:
    assembler = ResponseAssembler()
    consume(
        assembler,
        ToolCallStart(0, "call-1", "read"),
        ToolCallDelta(0, '{"path":"a.py"}'),
    )

    with pytest.raises(MessageAssemblyError, match="不一致"):
        assembler.consume(ToolCallComplete(0, ToolCallBlock("call-1", "read", {"path": "b.py"})))


def test_error_does_not_include_raw_tool_json() -> None:
    assembler = ResponseAssembler()
    consume(
        assembler,
        ToolCallStart(0, "call-1", "read"),
        ToolCallDelta(0, '{"secret":"must-not-leak"'),
    )
    with pytest.raises(MessageAssemblyError) as caught:
        assembler.consume(ToolCallComplete(0, ToolCallBlock("call-1", "read", {})))
    assert "must-not-leak" not in str(caught.value)


def test_finish_requires_stream_end_and_is_single_use() -> None:
    assembler = ResponseAssembler()
    assembler.consume(TextDelta(0, "ok"))
    with pytest.raises(MessageAssemblyError, match="缺少"):
        assembler.finish()

    assembler.consume(StreamEnd(StopReason.END_TURN))
    assert assembler.finish().text == "ok"
    with pytest.raises(MessageAssemblyError, match="已经"):
        assembler.finish()


def test_rejects_duplicate_stream_end_and_event_after_end() -> None:
    first = ResponseAssembler()
    consume(first, TextDelta(0, "ok"), StreamEnd(StopReason.END_TURN))
    with pytest.raises(MessageAssemblyError, match="结束后"):
        first.consume(StreamEnd(StopReason.END_TURN))

    second = ResponseAssembler()
    consume(second, TextDelta(0, "ok"), StreamEnd(StopReason.END_TURN))
    with pytest.raises(MessageAssemblyError, match="结束后"):
        second.consume(TextDelta(0, "late"))
