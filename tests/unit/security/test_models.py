import pytest
from pydantic import ValidationError

from ycode.core.messages import ToolCallBlock
from ycode.security import (
    ArgumentMatcher,
    PermissionAction,
    PermissionDecision,
    PermissionMode,
    PermissionSession,
    PermissionSubject,
    SecurityConfig,
    SecurityConfigLoadResult,
    SecurityConfigWarning,
    SecurityRule,
)


def test_security_config_rejects_invalid_matchers_and_duplicate_ids() -> None:
    with pytest.raises(ValidationError, match="只能声明 exact 或 glob"):
        ArgumentMatcher(exact="a", glob="*")
    with pytest.raises(ValidationError, match="exact 不支持 null"):
        ArgumentMatcher(exact=None)
    with pytest.raises(ValidationError, match="安全规则 ID 重复"):
        SecurityConfig(
            rules=(
                SecurityRule(id="same", action="allow", tool="read_file"),
                SecurityRule(id="same", action="deny", tool="read_file"),
            )
        )


def test_security_load_result_has_typed_immutable_warnings() -> None:
    warning = SecurityConfigWarning("unavailable", "mcp_demo_echo", "当前不可用")
    result = SecurityConfigLoadResult(SecurityConfig(), (warning,))

    assert result.warnings == (warning,)


def test_permission_subject_freezes_inputs_without_aliasing() -> None:
    arguments = {"path": "one.txt"}
    session_key = {"tool": "read_file", "path": "one.txt"}
    subject = PermissionSubject(
        call=ToolCallBlock(id="call-1", name="read_file", arguments=arguments),
        normalized_arguments=arguments,
        session_key=session_key,
        approval_summary="读取 one.txt",
    )
    arguments["path"] = "changed.txt"
    session_key["path"] = "changed.txt"

    assert subject.normalized_arguments["path"] == "one.txt"
    assert subject.session_key["path"] == "one.txt"
    with pytest.raises(TypeError):
        subject.session_key["path"] = "other.txt"  # type: ignore[index]


def test_permission_session_switches_mode_and_manages_exact_grants() -> None:
    session = PermissionSession()
    first = {"tool": "run_command", "command": "git status", "cwd": "."}
    reordered = {"cwd": ".", "command": "git status", "tool": "run_command"}
    changed = {"tool": "run_command", "command": "git diff", "cwd": "."}

    session.grant(first)

    assert session.allows(reordered)
    assert not session.allows(changed)
    assert session.grant_count == 1
    session.set_mode(PermissionMode.ALLOW)
    assert session.mode is PermissionMode.ALLOW
    session.clear()
    assert session.grant_count == 0


def test_permission_decision_requires_typed_action_and_reason() -> None:
    subject = PermissionSubject(
        call=ToolCallBlock(id="call-1", name="read_file", arguments={"path": "a"}),
        normalized_arguments={"path": "a"},
        session_key={"tool": "read_file", "path": "a"},
        approval_summary="读取 a",
    )

    decision = PermissionDecision(
        action=PermissionAction.ASK,
        subject=subject,
        reason_code="mode_default",
        message="需要确认",
    )

    assert decision.action is PermissionAction.ASK
    with pytest.raises(ValueError, match="必须携带原因"):
        PermissionDecision(
            action=PermissionAction.DENY,
            subject=subject,
            reason_code="",
            message="",
        )
