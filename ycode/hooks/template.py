"""Hook 简单模板替换。"""

import json
import re
from xml.sax.saxutils import escape

from ycode.hooks.matching import MISSING, resolve_hook_path

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


def render_hook_template(template: str, context: object) -> str:
    def replace(match: re.Match[str]) -> str:
        value = resolve_hook_path(context, match.group(1))
        return "" if value is MISSING else _render_value(value)

    return _PLACEHOLDER.sub(replace, template)


def escape_reminder_text(value: str) -> str:
    return escape(value)


def _render_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
