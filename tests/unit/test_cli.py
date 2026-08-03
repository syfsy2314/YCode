from unittest.mock import AsyncMock

import pytest

import ycode.cli
from ycode.errors import ConfigError


def test_cli_passes_explicit_config(monkeypatch: pytest.MonkeyPatch) -> None:
    run_app = AsyncMock()
    monkeypatch.setattr(ycode.cli, "run_app", run_app)

    assert ycode.cli.main(["--config", "custom.yaml"]) == 0
    run_app.assert_awaited_once_with("custom.yaml")


def test_cli_passes_continue_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    run_app = AsyncMock()
    monkeypatch.setattr(ycode.cli, "run_app", run_app)

    assert ycode.cli.main(["--continue"]) == 0
    run_app.assert_awaited_once_with(None, continue_session=True)


def test_cli_config_error_is_safe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_app = AsyncMock(side_effect=ConfigError("找不到配置"))
    monkeypatch.setattr(ycode.cli, "run_app", run_app)

    assert ycode.cli.main([]) == 2
    captured = capsys.readouterr()
    assert "找不到配置" in captured.err
    assert "Traceback" not in captured.err


def test_cli_keyboard_interrupt_is_normal_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    run_app = AsyncMock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr(ycode.cli, "run_app", run_app)
    assert ycode.cli.main([]) == 0


def test_help_does_not_start_application(monkeypatch: pytest.MonkeyPatch) -> None:
    run_app = AsyncMock()
    monkeypatch.setattr(ycode.cli, "run_app", run_app)
    with pytest.raises(SystemExit) as caught:
        ycode.cli.main(["--help"])
    assert caught.value.code == 0
    run_app.assert_not_called()
