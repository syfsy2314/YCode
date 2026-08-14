"""工具调用的统一权限判定。"""

import fnmatch
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ycode.skills.models import SkillTaskScope
    from ycode.skills.runtime import SkillRuntime

from ycode.core.messages import ToolCallBlock, freeze_json, thaw_json
from ycode.security.models import (
    ArgumentMatcher,
    PermissionAction,
    PermissionDecision,
    PermissionMode,
    PermissionPreparation,
    PermissionSession,
    PermissionSubject,
    SecurityConfig,
    SecurityRule,
)
from ycode.security.powershell import PowerShellSafetyChecker
from ycode.tools import ToolAccess, ToolRegistry
from ycode.tools.arguments import ToolArgumentValidationError
from ycode.tools.errors import ToolError
from ycode.tools.paths import WorkspacePathResolver

_PREVIEW_BYTES = 2 * 1024
_PATH_ARGUMENTS = frozenset({"path", "cwd"})


class PermissionEngine:
    def __init__(
        self,
        registry: ToolRegistry,
        resolver: WorkspacePathResolver,
        config: SecurityConfig,
        command_checker: PowerShellSafetyChecker,
        skill_runtime: "SkillRuntime | None" = None,
    ) -> None:
        self._registry = registry
        self._resolver = resolver
        self._config = config
        self._command_checker = command_checker
        self._plan_only_mcp_tools = frozenset(config.plan_only.allow_mcp_tools)
        self._skill_runtime = skill_runtime

    async def evaluate(
        self,
        call: ToolCallBlock,
        session: PermissionSession,
        *,
        allowed_access: frozenset[ToolAccess],
        plan_only: bool = False,
        skill_scope: "SkillTaskScope | None" = None,
    ) -> PermissionDecision:
        preparation = await self.prepare(
            call,
            allowed_access=allowed_access,
            plan_only=plan_only,
        )
        if preparation.denial is not None:
            return preparation.denial
        return self.evaluate_policy(preparation, session, skill_scope=skill_scope)

    async def prepare(
        self,
        call: ToolCallBlock,
        *,
        allowed_access: frozenset[ToolAccess],
        plan_only: bool = False,
    ) -> PermissionPreparation:
        try:
            tool = self._registry.get(call.name)
            if tool is None:
                denial = self._fallback_denial(
                    call,
                    "unknown_tool",
                    "工具未注册，已拒绝执行。",
                )
                return PermissionPreparation(denial.subject, denial, plan_only)
            subject = self._normalize(call, tool.definition.access)

            if call.name == "run_command":
                command = str(subject.normalized_arguments["command"])
                safety = await self._command_checker.check(command)
                if not safety.safe:
                    denial = PermissionDecision(
                        PermissionAction.DENY,
                        subject,
                        f"hard_{safety.category}",
                        safety.message,
                    )
                    return PermissionPreparation(subject, denial, plan_only)
            if tool.definition.access not in allowed_access:
                if not (
                    plan_only
                    and tool.definition.name in self._plan_only_mcp_tools
                    and tool.definition.access is ToolAccess.UNKNOWN
                    and tool.definition.defer_loading
                ):
                    denial = PermissionDecision(
                        PermissionAction.DENY,
                        subject,
                        "access_not_available",
                        "当前模式不允许执行该工具。",
                    )
                    return PermissionPreparation(subject, denial, plan_only)
            return PermissionPreparation(subject, plan_only=plan_only)
        except (ToolArgumentValidationError, ToolError, TypeError, ValueError, KeyError):
            denial = self._fallback_denial(
                call,
                "invalid_or_unsafe_arguments",
                "工具参数无法安全验证，已拒绝执行。",
            )
            return PermissionPreparation(denial.subject, denial, plan_only)
        except Exception:
            denial = self._fallback_denial(
                call,
                "permission_internal_error",
                "权限检查失败，已拒绝执行。",
            )
            return PermissionPreparation(denial.subject, denial, plan_only)

    def evaluate_policy(
        self,
        preparation: PermissionPreparation,
        session: PermissionSession,
        *,
        skill_scope: "SkillTaskScope | None" = None,
    ) -> PermissionDecision:
        subject = preparation.subject
        call = subject.call
        try:
            tool = self._registry.get(call.name)
            if tool is None:
                return self._fallback_denial(call, "unknown_tool", "工具未注册，已拒绝执行。")
            rule = self._first_matching_rule(call.name, subject.normalized_arguments)
            if rule is not None and rule.action is PermissionAction.DENY:
                return PermissionDecision(
                    rule.action,
                    subject,
                    "project_rule",
                    _action_message(rule.action, f"项目规则 {rule.id}"),
                    rule.id,
                )
            if call.name == "install_skill":
                return PermissionDecision(
                    PermissionAction.ASK,
                    subject,
                    "skill_install_approval",
                    "安装 Skill 将写入项目目录，需要本次人工确认。",
                    allow_session=False,
                )
            activation = self._skill_activation_decision(call, subject, skill_scope)
            if activation is not None:
                return activation
            if (
                preparation.plan_only
                and tool.definition.name in self._plan_only_mcp_tools
                and tool.definition.access is ToolAccess.UNKNOWN
                and tool.definition.defer_loading
            ):
                decision = PermissionDecision(
                    PermissionAction.ASK,
                    subject,
                    "plan_only_mcp_approval",
                    "plan-only 模式下 MCP 工具每次都需要确认。",
                    allow_session=False,
                )
                return self._apply_skill_preapproval(decision, call.name, skill_scope)
            if session.allows(subject.session_key):
                return PermissionDecision(
                    PermissionAction.ALLOW,
                    subject,
                    "session_grant",
                    "本会话已允许相同工具调用。",
                )
            if rule is not None:
                decision = PermissionDecision(
                    rule.action,
                    subject,
                    "project_rule",
                    _action_message(rule.action, f"项目规则 {rule.id}"),
                    rule.id,
                )
                return self._apply_skill_preapproval(decision, call.name, skill_scope)
            action = _mode_action(session.mode, tool.definition.access)
            decision = PermissionDecision(
                action,
                subject,
                f"mode_{session.mode.value}",
                _action_message(action, f"{session.mode.value} 权限模式"),
            )
            return self._apply_skill_preapproval(decision, call.name, skill_scope)
        except (ToolArgumentValidationError, ToolError, TypeError, ValueError, KeyError):
            return self._fallback_denial(
                call,
                "invalid_or_unsafe_arguments",
                "工具参数无法安全验证，已拒绝执行。",
            )
        except Exception:
            return self._fallback_denial(
                call,
                "permission_internal_error",
                "权限检查失败，已拒绝执行。",
            )

    def _skill_activation_decision(
        self,
        call: ToolCallBlock,
        subject: PermissionSubject,
        scope: "SkillTaskScope | None",
    ) -> PermissionDecision | None:
        if call.name != "load_skill" or self._skill_runtime is None or scope is None:
            return None
        from ycode.skills.models import SkillInvocationSource

        snapshot = self._skill_runtime.load_current(str(subject.normalized_arguments["name"]))
        source = (
            SkillInvocationSource.NESTED if scope.call_stack else SkillInvocationSource.AUTOMATIC
        )
        if not self._skill_runtime.needs_activation_approval(snapshot, source):
            return None
        tools = ", ".join(sorted(snapshot.config.allowed_tools))
        approval_subject = PermissionSubject(
            call=subject.call,
            normalized_arguments=subject.normalized_arguments,
            session_key=subject.session_key,
            approval_summary=f"Skill: {snapshot.name}\n预授权工具: {tools}",
        )
        return PermissionDecision(
            PermissionAction.ASK,
            approval_subject,
            "skill_activation_approval",
            "Skill 请求为本次任务预授权工具。",
            allow_session=False,
        )

    @staticmethod
    def _apply_skill_preapproval(
        decision: PermissionDecision,
        tool_name: str,
        scope: "SkillTaskScope | None",
    ) -> PermissionDecision:
        if (
            decision.action is not PermissionAction.ASK
            or scope is None
            or tool_name not in scope.preapproved_tools
        ):
            return decision
        return PermissionDecision(
            PermissionAction.ALLOW,
            decision.subject,
            "skill_preapproval",
            "当前 Skill 已为本次任务预授权此工具。",
            decision.rule_id,
            decision.allow_session,
        )

    def _normalize(
        self,
        call: ToolCallBlock,
        access: ToolAccess,
    ) -> PermissionSubject:
        tool = self._registry.get(call.name)
        if tool is None:
            raise ValueError("工具未注册")
        raw = thaw_json(call.arguments)
        if not isinstance(raw, Mapping):
            raise TypeError("工具参数必须是 JSON object")
        validated = tool.definition.arguments.validate(raw)
        arguments = thaw_json(tool.definition.arguments.to_mapping(validated))
        if not isinstance(arguments, dict):
            raise TypeError("规范化参数不是 JSON object")
        self._normalize_paths(call.name, arguments)
        session_key = _session_key(call.name, arguments, access)
        normalized = freeze_json(arguments)
        frozen_key = freeze_json(session_key)
        if not isinstance(normalized, Mapping) or not isinstance(frozen_key, Mapping):
            raise TypeError("规范化参数不是 JSON object")
        return PermissionSubject(
            call=call,
            normalized_arguments=normalized,
            session_key=frozen_key,
            approval_summary=_approval_summary(call.name, arguments),
        )

    def _normalize_paths(self, tool_name: str, arguments: dict[str, Any]) -> None:
        if tool_name == "read_file":
            path = self._resolver.resolve_existing_file(arguments["path"])
            arguments["path"] = self._resolver.relative_display(path)
        elif tool_name == "edit_file":
            try:
                path = self._resolver.resolve_existing_file(arguments["path"])
            except ToolError as error:
                if error.code != "path_not_found":
                    raise
                path = self._resolver.resolve_write_target(arguments["path"])
            arguments["path"] = self._resolver.relative_display(path)
        elif tool_name == "write_file":
            path = self._resolver.resolve_write_target(arguments["path"])
            arguments["path"] = self._resolver.relative_display(path)
        elif tool_name == "grep":
            try:
                path = self._resolver.resolve_existing_file(arguments["path"])
            except ToolError as error:
                if error.code != "not_a_file":
                    raise
                path = self._resolver.resolve_existing_directory(arguments["path"])
            arguments["path"] = self._resolver.relative_display(path)
        elif tool_name == "run_command":
            cwd = self._resolver.resolve_existing_directory(arguments["cwd"])
            arguments["cwd"] = self._resolver.relative_display(cwd)
            arguments["command"] = arguments["command"].strip()

    def _first_matching_rule(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> SecurityRule | None:
        for rule in self._config.rules:
            if rule.tool != tool_name:
                continue
            if all(
                _matches_argument(name, arguments.get(name), matcher)
                for name, matcher in rule.arguments.items()
            ):
                return rule
        return None

    @staticmethod
    def _fallback_denial(
        call: ToolCallBlock,
        reason_code: str,
        message: str,
    ) -> PermissionDecision:
        raw = thaw_json(call.arguments)
        if not isinstance(raw, dict):
            raw = {}
        subject = PermissionSubject(
            call=call,
            normalized_arguments=raw,
            session_key={"tool": call.name, "arguments": raw},
            approval_summary=_bounded_json(raw),
        )
        return PermissionDecision(
            PermissionAction.DENY,
            subject,
            reason_code,
            message,
        )


def _session_key(
    tool_name: str,
    arguments: dict[str, Any],
    access: ToolAccess,
) -> dict[str, object]:
    fields: dict[str, tuple[str, ...]] = {
        "read_file": ("path",),
        "write_file": ("path", "overwrite"),
        "edit_file": ("path",),
        "glob": ("pattern",),
        "grep": ("pattern", "path", "file_pattern", "case_sensitive"),
        "run_command": ("command", "cwd"),
    }
    if access is ToolAccess.UNKNOWN or tool_name not in fields:
        return {"tool": tool_name, "arguments": arguments}
    return {
        "tool": tool_name,
        **{name: arguments[name] for name in fields[tool_name]},
    }


def _approval_summary(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "run_command":
        return f"cwd: {arguments['cwd']}\ncommand:\n{arguments['command']}"
    preview = dict(arguments)
    if tool_name == "write_file":
        preview["content"] = _bounded_text(str(arguments["content"]))
    elif tool_name == "edit_file":
        preview["old_text"] = _bounded_text(str(arguments["old_text"]))
        preview["new_text"] = _bounded_text(str(arguments["new_text"]))
    return _bounded_json(preview)


def _bounded_text(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= _PREVIEW_BYTES:
        return value
    suffix = "\n…（内容已截断）"
    budget = _PREVIEW_BYTES - len(suffix.encode("utf-8"))
    return encoded[:budget].decode("utf-8", errors="ignore") + suffix


def _bounded_json(value: object) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    return _bounded_text(rendered)


def _matches_argument(
    name: str,
    actual: object,
    matcher: ArgumentMatcher,
) -> bool:
    if "exact" in matcher.model_fields_set:
        return actual == matcher.exact
    if not isinstance(actual, str) or matcher.glob is None:
        return False
    pattern = matcher.glob
    if name in _PATH_ARGUMENTS:
        actual = actual.replace("\\", "/").casefold()
        pattern = pattern.replace("\\", "/").casefold()
    return fnmatch.fnmatchcase(actual, pattern)


def _mode_action(mode: PermissionMode, access: ToolAccess) -> PermissionAction:
    if access is ToolAccess.UNKNOWN:
        return PermissionAction.ASK
    if mode is PermissionMode.STRICT:
        return PermissionAction.ASK
    if mode is PermissionMode.ALLOW:
        return PermissionAction.ALLOW
    return PermissionAction.ALLOW if access is ToolAccess.READ else PermissionAction.ASK


def _action_message(action: PermissionAction, source: str) -> str:
    return {
        PermissionAction.ALLOW: f"{source}允许此工具调用。",
        PermissionAction.DENY: f"{source}拒绝此工具调用。",
        PermissionAction.ASK: f"{source}要求用户确认此工具调用。",
    }[action]
