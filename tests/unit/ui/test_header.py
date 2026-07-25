from io import StringIO

from rich.console import Console

from ycode.config.models import ProviderConfig
from ycode.ui.header import render_header


def config() -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "name": "local-claude",
            "protocol": "anthropic",
            "model": "claude-test",
            "base_url": "http://localhost:9000",
            "api_key": "placeholder",
            "thinking": True,
        }
    )


def render(width: int) -> str:
    target = StringIO()
    console = Console(file=target, width=width, color_system=None)
    console.print(render_header(config(), width))
    return target.getvalue()


def test_wide_header_contains_cat_and_details_without_config_path() -> None:
    output = render(100)
    assert "/\\_/\\" in output
    assert "YCode" in output
    assert "Provider" in output and "local-claude" in output
    assert "Protocol" in output and "anthropic" in output
    assert "Model" in output and "claude-test" in output
    assert "Thinking" in output and "on" in output
    assert "Config" not in output


def test_narrow_header_stacks_details_below_cat() -> None:
    output = render(35)
    assert output.index("Provider") > output.index("> ^ <")
    assert "local-claude" in output
