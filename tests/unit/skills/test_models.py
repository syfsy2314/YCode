from pathlib import Path

import pytest

from ycode.agent import AgentMode
from ycode.skills import (
    SkillCatalogEntry,
    SkillConfig,
    SkillContextKind,
    SkillExecutionMode,
    SkillProblem,
    SkillProblemSeverity,
    SkillSnapshot,
    SkillTaskAuthorization,
    SkillTaskScope,
)


def _snapshot(name: str = "review", config: SkillConfig | None = None) -> SkillSnapshot:
    root = Path("C:/workspace/.ycode/skills") / name
    return SkillSnapshot(
        name,
        "Review current changes.",
        root,
        root / "SKILL.md",
        "Follow the review procedure.",
        config or SkillConfig(),
        fingerprint="a" * 64,
    )


def test_standard_config_defaults_to_shared_current_context() -> None:
    config = SkillConfig()

    assert config.execution_mode is SkillExecutionMode.SHARED
    assert config.context_kind is SkillContextKind.CURRENT
    assert config.visible_tools is None
    assert config.allowed_tools == frozenset()


def test_isolated_recent_requires_positive_turn_count() -> None:
    with pytest.raises(ValueError, match="正整数"):
        SkillConfig(
            SkillExecutionMode.ISOLATED,
            context_kind=SkillContextKind.RECENT,
        )


def test_shared_skill_rejects_model_override() -> None:
    with pytest.raises(ValueError, match="共享 Skill 不能指定模型"):
        SkillConfig(model_name="review-model")


def test_catalog_entry_can_be_available_with_warning() -> None:
    snapshot = _snapshot()
    entry = SkillCatalogEntry(
        "review",
        snapshot.source_path,
        snapshot,
        (
            SkillProblem(
                "unsupported_tool_expression",
                "参数级授权未生效",
                SkillProblemSeverity.WARNING,
            ),
        ),
    )

    assert entry.available


def test_unavailable_entry_requires_error() -> None:
    with pytest.raises(ValueError, match="必须包含 error"):
        SkillCatalogEntry("broken", Path("broken/SKILL.md"), None)


def test_isolated_branch_shares_authorization_but_not_pending_state() -> None:
    authorization = SkillTaskAuthorization({"read_file"})
    parent = SkillTaskScope(AgentMode.AGENT, authorization=authorization)
    child = SkillTaskScope(
        AgentMode.AGENT,
        authorization=authorization,
        main_branch=False,
    )
    parent.pending_shared["parent"] = _snapshot("parent")
    child.pending_shared["child"] = _snapshot("child")

    child.preapproved_tools.add("grep")

    assert parent.preapproved_tools == {"read_file", "grep"}
    assert set(parent.pending_shared) == {"parent"}
    assert set(child.pending_shared) == {"child"}
