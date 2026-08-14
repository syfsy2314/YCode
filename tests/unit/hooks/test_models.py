import pytest
from pydantic import ValidationError

from ycode.hooks.models import HookRule


def test_rule_defaults_and_enabled() -> None:
    rule = HookRule.model_validate(
        {"id": "demo", "event": "session.start", "action": {"type": "agent"}}
    )
    assert rule.enabled is True
    assert rule.once is False
    assert rule.async_ is False


@pytest.mark.parametrize(
    "raw",
    [
        {
            "id": "bad-reminder",
            "event": "session.end",
            "action": {"type": "reminder", "content": "x"},
        },
        {
            "id": "bad-permission",
            "event": "session.start",
            "permission": "deny",
            "action": {"type": "agent"},
        },
        {
            "id": "bad-async",
            "event": "tool.before_execute",
            "permission": "ask",
            "async": True,
            "action": {"type": "shell", "command": "echo ok"},
        },
    ],
)
def test_invalid_rule_combinations(raw: object) -> None:
    with pytest.raises(ValidationError):
        HookRule.model_validate(raw)


def test_not_matcher_cannot_nest() -> None:
    with pytest.raises(ValidationError):
        HookRule.model_validate(
            {
                "id": "nested-not",
                "event": "session.start",
                "conditions": {"all": {"event.name": {"not": {"not": {"exact": "x"}}}}},
                "action": {"type": "agent"},
            }
        )
