"""工具权限模型与会话状态。"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ycode.core.messages import FrozenJsonObject, ToolCallBlock, freeze_json, thaw_json


class PermissionMode(StrEnum):
    STRICT = "strict"
    DEFAULT = "default"
    ALLOW = "allow"


class PermissionAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ApprovalChoice(StrEnum):
    DENY = "deny"
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"


class ArgumentMatcher(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exact: str | int | bool | None = None
    glob: str | None = None

    @model_validator(mode="after")
    def validate_matcher(self) -> "ArgumentMatcher":
        fields = self.model_fields_set
        if ("exact" in fields) == ("glob" in fields):
            raise ValueError("参数匹配必须且只能声明 exact 或 glob")
        if "exact" in fields and self.exact is None:
            raise ValueError("exact 不支持 null")
        if "glob" in fields and not self.glob:
            raise ValueError("glob 不能为空")
        return self


class SecurityRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    action: PermissionAction
    tool: str = Field(min_length=1)
    arguments: dict[str, ArgumentMatcher] = Field(default_factory=dict)


class PlanOnlySecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_mcp_tools: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_names(self) -> "PlanOnlySecurityConfig":
        if len(set(self.allow_mcp_tools)) != len(self.allow_mcp_tools):
            raise ValueError("allow_mcp_tools 不允许重复")
        if any(not name.startswith("mcp_") for name in self.allow_mcp_tools):
            raise ValueError("allow_mcp_tools 只能包含 mcp_* 工具")
        return self


class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: PermissionMode = PermissionMode.DEFAULT
    rules: tuple[SecurityRule, ...] = ()
    plan_only: PlanOnlySecurityConfig = Field(default_factory=PlanOnlySecurityConfig)

    @model_validator(mode="after")
    def validate_rule_ids(self) -> "SecurityConfig":
        ids = [rule.id for rule in self.rules]
        duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if duplicates:
            raise ValueError(f"安全规则 ID 重复：{', '.join(duplicates)}")
        return self


@dataclass(frozen=True, slots=True)
class SecurityConfigWarning:
    code: str
    tool_name: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.tool_name or not self.message:
            raise ValueError("安全配置警告必须包含错误码、工具名和消息")


@dataclass(frozen=True, slots=True)
class SecurityConfigLoadResult:
    config: SecurityConfig
    warnings: tuple[SecurityConfigWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class PermissionSubject:
    call: ToolCallBlock
    normalized_arguments: FrozenJsonObject
    session_key: FrozenJsonObject
    approval_summary: str

    def __post_init__(self) -> None:
        if not isinstance(self.call, ToolCallBlock):
            raise TypeError("权限主题必须携带 ToolCallBlock")
        normalized_arguments = freeze_json(self.normalized_arguments)
        session_key = freeze_json(self.session_key)
        if not isinstance(normalized_arguments, Mapping) or not isinstance(session_key, Mapping):
            raise TypeError("权限参数必须是 JSON object")
        if not self.approval_summary:
            raise ValueError("审批摘要不能为空")
        object.__setattr__(self, "normalized_arguments", normalized_arguments)
        object.__setattr__(self, "session_key", session_key)


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    action: PermissionAction
    subject: PermissionSubject
    reason_code: str
    message: str
    rule_id: str = ""
    allow_session: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.action, PermissionAction):
            raise TypeError("权限决策动作无效")
        if not isinstance(self.subject, PermissionSubject):
            raise TypeError("权限决策主题无效")
        if not self.reason_code or not self.message:
            raise ValueError("权限决策必须携带原因")


@dataclass(frozen=True, slots=True)
class PermissionPreparation:
    subject: PermissionSubject
    denial: PermissionDecision | None = None
    plan_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.subject, PermissionSubject):
            raise TypeError("权限预检必须携带 PermissionSubject")
        if self.denial is not None and not isinstance(self.denial, PermissionDecision):
            raise TypeError("权限预检拒绝必须是 PermissionDecision")


class PermissionSession:
    def __init__(self, mode: PermissionMode = PermissionMode.DEFAULT) -> None:
        if not isinstance(mode, PermissionMode):
            raise TypeError("权限模式无效")
        self._mode = mode
        self._grants: set[str] = set()

    @property
    def mode(self) -> PermissionMode:
        return self._mode

    @property
    def grant_count(self) -> int:
        return len(self._grants)

    def set_mode(self, mode: PermissionMode) -> None:
        if not isinstance(mode, PermissionMode):
            raise TypeError("权限模式无效")
        self._mode = mode

    def allows(self, session_key: FrozenJsonObject) -> bool:
        return _grant_key(session_key) in self._grants

    def grant(self, session_key: FrozenJsonObject) -> None:
        self._grants.add(_grant_key(session_key))

    def clear(self) -> None:
        self._grants.clear()


def _grant_key(value: FrozenJsonObject) -> str:
    if not isinstance(value, Mapping):
        raise TypeError("会话授权键必须是 JSON object")
    return json.dumps(
        thaw_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
