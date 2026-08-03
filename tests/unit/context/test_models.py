import pytest

from ycode.context import (
    ContextCommit,
    ContextCompactionReport,
    ContextFailureReport,
    ContextPolicy,
    ConversationMemory,
    SummarySource,
    TokenEstimate,
)
from ycode.core import ChatMessage


def test_context_policy_defaults() -> None:
    policy = ContextPolicy()

    assert policy.context_window_tokens == 200_000
    assert policy.auto_compact_threshold == 167_000
    assert policy.continue_request_limit == 180_000
    assert policy.single_tool_result_bytes == 50 * 1024
    assert policy.message_tool_results_bytes == 200 * 1024
    assert policy.preview_bytes == 4 * 1024


def test_context_policy_uses_custom_window() -> None:
    policy = ContextPolicy(100_000)

    assert policy.auto_compact_threshold == 67_000
    assert policy.continue_request_limit == 80_000


@pytest.mark.parametrize("value", [True, "200000", 200_000.0, 33_000])
def test_context_policy_rejects_invalid_window(value: object) -> None:
    with pytest.raises(ValueError, match="大于 33000 的整数"):
        ContextPolicy(value)  # type: ignore[arg-type]


def test_context_models_freeze_history_and_compute_total() -> None:
    user = ChatMessage.user_text("原始请求")
    messages = [user]
    memory = ConversationMemory("## 主要请求\n保留请求")
    commit = ContextCommit(messages, memory)  # type: ignore[arg-type]
    source = SummarySource(memory, messages, user)  # type: ignore[arg-type]
    estimate = TokenEstimate(local_tokens=100, calibrated_tokens=120)

    messages.clear()

    assert commit.history == (user,)
    assert source.messages == (user,)
    assert estimate.total_tokens == 120


def test_context_reports_validate_counts() -> None:
    assert ContextCompactionReport(200, 100).before_tokens == 200
    assert ContextFailureReport("summary_invalid", "摘要无效", 3, True, False).fuse_open

    with pytest.raises(ValueError, match="非负整数"):
        TokenEstimate(-1, 0)
    with pytest.raises(ValueError, match="连续失败次数"):
        ContextFailureReport("summary_invalid", "摘要无效", -1, False, False)


def test_summary_source_requires_user_retained_message() -> None:
    with pytest.raises(ValueError, match="必须来自用户"):
        SummarySource(None, (), ChatMessage.assistant_text("answer"))
