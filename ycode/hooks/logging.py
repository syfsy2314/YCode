"""Hook 日志辅助。"""

import logging

logger = logging.getLogger("ycode.hooks")
_SUMMARY_LIMIT = 2048


def bounded_summary(value: object) -> str:
    text = str(value)
    if len(text) <= _SUMMARY_LIMIT:
        return text
    return text[: _SUMMARY_LIMIT - 1] + "…"


def log_hook_result(event: str, rule_id: str, action: str, result: str, message: str = "") -> None:
    logger.info(
        "hook event=%s rule=%s action=%s result=%s message=%s",
        event,
        rule_id,
        action,
        result,
        bounded_summary(message),
    )
