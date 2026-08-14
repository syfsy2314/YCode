from ycode.hooks.matching import matches_hook_conditions, resolve_hook_path
from ycode.hooks.models import HookConditions


def test_resolve_nested_and_array_path() -> None:
    context = {"tool": {"arguments": {"files": ["a.py", "b.py"]}}}
    assert resolve_hook_path(context, "tool.arguments.files.1") == "b.py"


def test_match_all_operators_and_missing_not() -> None:
    context = {
        "tool": {
            "name": "run_command",
            "alias": "run_command",
            "command": "deploy prod",
        }
    }
    conditions = HookConditions.model_validate(
        {
            "all": {
                "tool.name": {"exact": "run_command"},
                "tool.command": {"regex": "deploy"},
                "tool.alias": {"glob": "run_*"},
            }
        }
    )
    assert matches_hook_conditions(conditions, context)
    missing_not = HookConditions.model_validate({"all": {"tool.missing": {"not": {"exact": "x"}}}})
    assert not matches_hook_conditions(missing_not, context)


def test_any_condition() -> None:
    conditions = HookConditions.model_validate(
        {"any": {"tool.name": {"exact": "write_file"}, "tool.path": {"glob": "*.py"}}}
    )
    assert matches_hook_conditions(conditions, {"tool": {"path": "main.py"}})
