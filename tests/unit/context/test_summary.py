import asyncio
import json

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.context import (
    ConversationCompactor,
    ConversationMemory,
    SummarySource,
    SummaryValidationError,
    build_transcript,
    load_summary_prompt,
    parse_summary_response,
)
from ycode.core import (
    ChatMessage,
    RedactedThinkingBlock,
    StopReason,
    StreamEnd,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolCallBlock,
    ToolCallStart,
    ToolResultBlock,
)

HEADINGS = (
    "主要请求",
    "关键概念",
    "文件代码",
    "错误修复",
    "解决过程",
    "用户原话",
    "待办",
    "当前工作",
    "下一步",
)


def response(user_section: str = "无") -> str:
    sections = []
    for heading in HEADINGS:
        value = user_section if heading == "用户原话" else "无"
        sections.append(f"## {heading}\n{value}")
    return (
        "<analysis_draft>简洁草稿</analysis_draft>\n<summary>\n"
        + "\n".join(sections)
        + "\n</summary>"
    )


def test_summary_prompt_forbids_tools_at_both_ends() -> None:
    prompt = load_summary_prompt().strip()

    assert "禁止调用任何工具" in prompt.splitlines()[0]
    assert "禁止调用任何工具" in prompt.splitlines()[-1]
    assert "<analysis_draft>" in prompt
    assert all(f"## {heading}" in prompt for heading in HEADINGS)


def test_transcript_is_stable_and_excludes_thinking() -> None:
    user = ChatMessage.user_text("请保持原文")
    assistant = ChatMessage(
        "assistant",
        (
            ThinkingBlock("private", "signature"),
            RedactedThinkingBlock("encrypted"),
            TextBlock("准备读取"),
            ToolCallBlock("call-1", "read_file", {"path": "a.py"}),
        ),
    )
    result = ChatMessage("user", (ToolResultBlock("call-1", "file content"),))
    source = SummarySource(ConversationMemory("old memory"), (user, assistant, result), user)

    first = build_transcript(source)
    second = build_transcript(source)

    assert first == second
    assert first.user_messages == {"U0001": "请保持原文"}
    assert '"id":"A0001"' in first.text
    assert '"id":"T0001"' in first.text
    assert '"id":"T0002"' in first.text
    assert "private" not in first.text
    assert "signature" not in first.text
    assert "encrypted" not in first.text


def test_parse_discards_draft_and_validates_exact_quote() -> None:
    original = "请保留标点，完全一致。"
    encoded = json.dumps(original, ensure_ascii=False)
    memory = parse_summary_response(
        response(f"- 原文 [U0001]: {encoded}"),
        {"U0001": original},
    )

    assert "简洁草稿" not in memory.summary
    assert "## 用户原话" in memory.summary

    with pytest.raises(SummaryValidationError, match="来源不一致"):
        parse_summary_response(
            response(f"- 原文 [U0001]: {json.dumps('被修改', ensure_ascii=False)}"),
            {"U0001": original},
        )


def test_parse_accepts_overview_but_rejects_bad_structure() -> None:
    memory = parse_summary_response(
        response("- 概述 [U0001]: 用户要求实现上下文管理"),
        {"U0001": "原始消息"},
    )
    assert "概述" in memory.summary

    with pytest.raises(SummaryValidationError, match="标题"):
        parse_summary_response(response().replace("## 下一步", "## 后续"), {})
    with pytest.raises(SummaryValidationError, match="草稿"):
        parse_summary_response(response().replace("简洁草稿", ""), {})


@pytest.mark.asyncio
async def test_compactor_sends_isolated_request_and_retains_latest_user() -> None:
    original = "请保留原文"
    encoded = json.dumps(original, ensure_ascii=False)
    provider = FakeProvider(
        [[TextDelta(0, response(f"- 原文 [U0001]: {encoded}")), StreamEnd(StopReason.END_TURN)]]
    )
    user = ChatMessage.user_text(original)

    result = await ConversationCompactor(provider).compact(SummarySource(None, (user,), user))

    request = provider.agent_requests[0]
    assert result.retained_messages == (user,)
    assert request.max_output_tokens == 20_000
    assert request.thinking_enabled is False
    assert request.tools == ()
    assert request.supplements == ()
    assert len(request.system_prompt) == 1
    assert request.messages[0].text.startswith("<summary_source>")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [ThinkingDelta(0, "reason")],
        [ToolCallStart(0, "call-1", "read_file")],
        [TextDelta(0, response()), StreamEnd(StopReason.MAX_TOKENS)],
        [TextDelta(0, response())],
    ],
)
async def test_compactor_rejects_invalid_stream(events: list[object]) -> None:
    provider = FakeProvider([events])  # type: ignore[arg-type]

    with pytest.raises(SummaryValidationError):
        await ConversationCompactor(provider).compact(
            SummarySource(None, (ChatMessage.user_text("hello"),))
        )
    assert len(provider.agent_requests) == 1


@pytest.mark.asyncio
async def test_compactor_propagates_cancellation() -> None:
    provider = FakeProvider(
        [[TextDelta(0, response()), StreamEnd(StopReason.END_TURN)]],
        delay=10,
    )
    task = asyncio.create_task(
        ConversationCompactor(provider).compact(
            SummarySource(None, (ChatMessage.user_text("hello"),))
        )
    )
    await provider.request_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
