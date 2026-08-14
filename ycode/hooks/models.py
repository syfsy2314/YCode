"""Hook 配置、事件与执行结果模型。"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ycode.core.messages import FrozenJsonObject, freeze_json


class HookEventName(StrEnum):
    SESSION_START = "session.start"
    SESSION_END = "session.end"
    TURN_START = "turn.start"
    TURN_END = "turn.end"
    MESSAGE_BEFORE_SEND = "message.before_send"
    MESSAGE_AFTER_RECEIVE = "message.after_receive"
    TOOL_BEFORE_EXECUTE = "tool.before_execute"
    TOOL_AFTER_EXECUTE = "tool.after_execute"
    CONTEXT_COMPACTED = "context.compacted"
    AGENT_ERROR = "agent.error"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class HookPermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class HookActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class HookPositiveMatcher(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exact: str | int | float | bool | None = None
    glob: str | None = None
    regex: str | None = None

    @model_validator(mode="after")
    def validate_operator(self) -> "HookPositiveMatcher":
        fields = self.model_fields_set
        selected = fields & {"exact", "glob", "regex"}
        if len(selected) != 1:
            raise ValueError("匹配器必须且只能声明 exact、glob 或 regex")
        if "exact" in selected and self.exact is None:
            raise ValueError("exact 不支持 null")
        if "glob" in selected and not self.glob:
            raise ValueError("glob 不能为空")
        if "regex" in selected:
            if not self.regex:
                raise ValueError("regex 不能为空")
            import re

            try:
                re.compile(self.regex)
            except re.error as error:
                raise ValueError(f"regex 无效：{error}") from error
        return self


class HookMatcher(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    exact: str | int | float | bool | None = None
    glob: str | None = None
    regex: str | None = None
    not_: HookPositiveMatcher | None = Field(default=None, alias="not")

    @model_validator(mode="after")
    def validate_operator(self) -> "HookMatcher":
        fields = self.model_fields_set
        selected = fields & {"exact", "glob", "regex", "not_"}
        if len(selected) != 1:
            raise ValueError("匹配器必须且只能声明 exact、glob、regex 或 not")
        if "not_" in selected:
            if self.not_ is None:
                raise ValueError("not 不能为空")
            return self
        HookPositiveMatcher.model_validate({name: getattr(self, name) for name in selected})
        return self


class HookConditions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all: dict[str, HookMatcher] | None = None
    any: dict[str, HookMatcher] | None = None

    @model_validator(mode="after")
    def validate_group(self) -> "HookConditions":
        selected = self.model_fields_set & {"all", "any"}
        if len(selected) != 1:
            raise ValueError("条件必须且只能声明 all 或 any")
        values = self.all if "all" in selected else self.any
        if not values:
            raise ValueError("条件组不能为空")
        if any(not path.strip() for path in values):
            raise ValueError("条件字段路径不能为空")
        return self


class ShellHookAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["shell"]
    command: str = Field(min_length=1)


class ReminderHookAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["reminder"]
    content: str = Field(min_length=1)


class HttpHookAction(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["http"]
    method: HttpMethod
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    json_: Any = Field(default=None, alias="json")

    @model_validator(mode="after")
    def validate_body(self) -> "HttpHookAction":
        if "body" in self.model_fields_set and "json_" in self.model_fields_set:
            raise ValueError("HTTP body 和 json 不能同时配置")
        if self.body is not None and not self.body:
            raise ValueError("HTTP body 不能为空")
        if "json_" in self.model_fields_set:
            freeze_json(self.json_)
        return self


class AgentHookAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["agent"]


HookAction = Annotated[
    ShellHookAction | ReminderHookAction | HttpHookAction | AgentHookAction,
    Field(discriminator="type"),
]


class HookRule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    event: HookEventName
    action: HookAction
    enabled: bool = True
    conditions: HookConditions | None = None
    permission: HookPermissionDecision | None = None
    once: bool = False
    async_: bool = Field(default=False, alias="async")
    timeout_seconds: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def validate_execution(self) -> "HookRule":
        if self.permission is not None and self.event is not HookEventName.TOOL_BEFORE_EXECUTE:
            raise ValueError("permission 只能用于 tool.before_execute")
        if self.async_ and not isinstance(self.action, ShellHookAction | HttpHookAction):
            raise ValueError("只有 shell 和 http 动作可以异步执行")
        if self.async_ and self.permission is not None:
            raise ValueError("参与权限决定的 Hook 不能异步执行")
        if isinstance(self.action, ReminderHookAction):
            if self.async_:
                raise ValueError("reminder 动作不能异步执行")
            if self.event is HookEventName.SESSION_END:
                raise ValueError("session.end 不支持 reminder 动作")
        return self


class ShellPermissionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permissionDecision: HookPermissionDecision
    permissionDecisionReason: str = ""


@dataclass(frozen=True, slots=True)
class HookDiagnostic:
    code: str
    path: str
    rule_index: int | None
    rule_id: str
    message: str


@dataclass(frozen=True, slots=True)
class HookConfigLoadResult:
    rules: tuple[HookRule, ...] = ()
    diagnostics: tuple[HookDiagnostic, ...] = ()
    external_action_warning: bool = False


@dataclass(frozen=True, slots=True)
class HookEvent:
    name: HookEventName
    context: FrozenJsonObject

    def __post_init__(self) -> None:
        frozen = freeze_json(self.context)
        if not isinstance(frozen, Mapping):
            raise TypeError("Hook 事件上下文必须是 JSON object")
        object.__setattr__(self, "context", frozen)


@dataclass(frozen=True, slots=True)
class HookActionResult:
    status: HookActionStatus
    permission: HookPermissionDecision | None = None
    reason: str = ""
    message: str = ""
    reminder: str = ""


@dataclass(frozen=True, slots=True)
class HookDispatchResult:
    permission: HookPermissionDecision | None = None
    reason: str = ""
    notices: tuple[str, ...] = ()
