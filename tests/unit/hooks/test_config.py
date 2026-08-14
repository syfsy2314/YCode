from ycode.hooks.config import (
    discover_hook_config,
    format_hook_diagnostic,
    load_hook_config,
)


def test_discovers_nearest_config(tmp_path) -> None:
    root = tmp_path / "root"
    child = root / "child"
    (root / ".ycode").mkdir(parents=True)
    child.mkdir()
    (root / ".ycode" / "hooks.yaml").write_text("hooks: []\n", encoding="utf-8")
    assert discover_hook_config(child) == root / ".ycode" / "hooks.yaml"


def test_load_skips_invalid_and_duplicate_rules(tmp_path) -> None:
    config_dir = tmp_path / ".ycode"
    config_dir.mkdir()
    (config_dir / "hooks.yaml").write_text(
        """
hooks:
  - id: valid
    event: session.start
    action: {type: agent}
  - id: invalid
    event: missing
    action: {type: agent}
  - id: valid
    event: session.end
    action: {type: shell, command: echo ok}
""",
        encoding="utf-8",
    )
    result = load_hook_config(tmp_path)
    assert [rule.id for rule in result.rules] == ["valid"]
    assert [item.code for item in result.diagnostics] == [
        "hook_rule_invalid",
        "hook_rule_duplicate",
    ]


def test_invalid_yaml_returns_diagnostic(tmp_path) -> None:
    config_dir = tmp_path / ".ycode"
    config_dir.mkdir()
    (config_dir / "hooks.yaml").write_text("hooks: [", encoding="utf-8")
    result = load_hook_config(tmp_path)
    assert result.rules == ()
    assert result.diagnostics[0].code == "hook_config_parse_error"


def test_diagnostic_message_contains_location_and_rule(tmp_path) -> None:
    config_dir = tmp_path / ".ycode"
    config_dir.mkdir()
    path = config_dir / "hooks.yaml"
    path.write_text(
        "hooks:\n  - id: broken\n    event: missing\n    action: {type: agent}\n",
        encoding="utf-8",
    )
    diagnostic = load_hook_config(tmp_path).diagnostics[0]

    message = format_hook_diagnostic(diagnostic)

    assert str(path) in message
    assert "规则 #1" in message
    assert "broken" in message
    assert "event" in message
