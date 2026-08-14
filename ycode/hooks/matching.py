"""Hook 点路径解析与条件匹配。"""

import fnmatch
import re
from collections.abc import Mapping, Sequence

from ycode.hooks.models import HookConditions, HookMatcher, HookPositiveMatcher


class _Missing:
    pass


MISSING = _Missing()


def resolve_hook_path(context: object, path: str) -> object | _Missing:
    current = context
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return MISSING
            current = current[part]
            continue
        if isinstance(current, Sequence) and not isinstance(current, str | bytes):
            if not part.isdecimal():
                return MISSING
            index = int(part)
            if index >= len(current):
                return MISSING
            current = current[index]
            continue
        return MISSING
    return current


def matches_hook_conditions(conditions: HookConditions | None, context: object) -> bool:
    if conditions is None:
        return True
    if conditions.all is not None:
        return all(
            _matches(resolve_hook_path(context, path), matcher)
            for path, matcher in conditions.all.items()
        )
    assert conditions.any is not None
    return any(
        _matches(resolve_hook_path(context, path), matcher)
        for path, matcher in conditions.any.items()
    )


def _matches(actual: object, matcher: HookMatcher) -> bool:
    if actual is MISSING:
        return False
    if matcher.not_ is not None:
        return not _matches_positive(actual, matcher.not_)
    positive = HookPositiveMatcher.model_validate(
        matcher.model_dump(by_alias=True, exclude_none=True)
    )
    return _matches_positive(actual, positive)


def _matches_positive(actual: object, matcher: HookPositiveMatcher) -> bool:
    if "exact" in matcher.model_fields_set:
        return actual == matcher.exact
    if not isinstance(actual, str):
        return False
    if matcher.glob is not None:
        return fnmatch.fnmatchcase(actual, matcher.glob)
    assert matcher.regex is not None
    return re.search(matcher.regex, actual) is not None
