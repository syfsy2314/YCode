"""首期固定终端样式。"""

from dataclasses import dataclass

from prompt_toolkit.styles import Style

BLUE = "bright_blue"
MUTED = "grey62"
ERROR = "red"


@dataclass(frozen=True, slots=True)
class InputBorderStyle:
    color: str = "ansibrightblack"


DEFAULT_INPUT_BORDER_STYLE = InputBorderStyle()


def create_prompt_style(
    border_style: InputBorderStyle = DEFAULT_INPUT_BORDER_STYLE,
) -> Style:
    return Style.from_dict(
        {
            "input-border": f"{border_style.color} bg:default noreverse",
            "input-hint": "ansibrightblack bg:default noreverse",
            "prompt": "ansibrightblue bold",
            "placeholder": "ansibrightblack",
        }
    )


PROMPT_STYLE = create_prompt_style()
