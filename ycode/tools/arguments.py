"""工具参数 Schema、校验与统一错误契约。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from jsonschema import Draft202012Validator, validators
from pydantic import BaseModel, ValidationError

from ycode.core.messages import FrozenJson, FrozenJsonObject, freeze_json, thaw_json


@dataclass(frozen=True, slots=True)
class ToolArgumentIssue:
    """不包含原始输入值的稳定参数校验详情。"""

    field: str
    message: str
    type: str


class ToolArgumentValidationError(Exception):
    """工具参数不符合声明 Schema。"""

    def __init__(self, details: tuple[ToolArgumentIssue, ...]) -> None:
        if not details:
            raise ValueError("参数校验错误必须包含详情")
        self.details = details
        super().__init__("工具参数校验失败")


@runtime_checkable
class ToolArguments[ArgumentsT](Protocol):
    """为不同 Schema 后端提供统一的参数适配接口。"""

    @property
    def input_schema(self) -> FrozenJsonObject: ...

    @property
    def field_names(self) -> frozenset[str]: ...

    def validate(self, raw: Mapping[str, FrozenJson]) -> ArgumentsT: ...

    def to_mapping(self, value: ArgumentsT) -> FrozenJsonObject: ...


@dataclass(frozen=True, slots=True)
class PydanticToolArguments[ArgumentsT: BaseModel]:
    """以 Pydantic 模型实现的内建工具参数适配器。"""

    model: type[ArgumentsT]
    _input_schema: FrozenJsonObject = field(init=False, repr=False)
    _field_names: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.model, type) or not issubclass(self.model, BaseModel):
            raise TypeError("工具参数模型必须继承 Pydantic BaseModel")
        schema = freeze_json(self.model.model_json_schema())
        if not isinstance(schema, Mapping):
            raise TypeError("工具参数 Schema 必须是 JSON object")
        object.__setattr__(self, "_input_schema", schema)
        object.__setattr__(self, "_field_names", frozenset(self.model.model_fields))

    @property
    def input_schema(self) -> FrozenJsonObject:
        return self._input_schema

    @property
    def field_names(self) -> frozenset[str]:
        return self._field_names

    def validate(self, raw: Mapping[str, FrozenJson]) -> ArgumentsT:
        try:
            return self.model.model_validate(thaw_json(freeze_json(raw)))
        except ValidationError as error:
            details = tuple(
                ToolArgumentIssue(
                    field=".".join(str(part) for part in item["loc"]),
                    message=item["msg"],
                    type=item["type"],
                )
                for item in sorted(
                    error.errors(include_url=False, include_input=False),
                    key=lambda item: (
                        tuple(str(part) for part in item["loc"]),
                        item["type"],
                        item["msg"],
                    ),
                )
            )
            raise ToolArgumentValidationError(details) from error

    def to_mapping(self, value: ArgumentsT) -> FrozenJsonObject:
        if not isinstance(value, self.model):
            raise TypeError("工具参数类型与定义不匹配")
        mapping = freeze_json(value.model_dump(mode="json"))
        if not isinstance(mapping, Mapping):
            raise TypeError("工具参数必须转换为 JSON object")
        return mapping


@dataclass(frozen=True, slots=True)
class JsonSchemaToolArguments:
    """以本地 JSON Schema 实现的 MCP 工具参数适配器。"""

    schema: FrozenJsonObject
    _input_schema: FrozenJsonObject = field(init=False, repr=False)
    _field_names: frozenset[str] = field(init=False, repr=False)
    _validator: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        frozen_schema = freeze_json(self.schema)
        if not isinstance(frozen_schema, Mapping):
            raise TypeError("工具参数 Schema 必须是 JSON object")
        _reject_external_references(frozen_schema)
        raw_schema = thaw_json(frozen_schema)
        if not isinstance(raw_schema, Mapping):
            raise TypeError("工具参数 Schema 必须是 JSON object")
        validator_class = validators.validator_for(raw_schema, default=Draft202012Validator)
        validator_class.check_schema(raw_schema)
        properties = raw_schema.get("properties", {})
        field_names = frozenset(properties) if isinstance(properties, Mapping) else frozenset()
        object.__setattr__(self, "_input_schema", frozen_schema)
        object.__setattr__(self, "_field_names", field_names)
        object.__setattr__(self, "_validator", validator_class(raw_schema))

    @property
    def input_schema(self) -> FrozenJsonObject:
        return self._input_schema

    @property
    def field_names(self) -> frozenset[str]:
        return self._field_names

    def validate(self, raw: Mapping[str, FrozenJson]) -> FrozenJsonObject:
        if not isinstance(raw, Mapping):
            raise _json_schema_validation_error("", "工具参数必须是 JSON object", "type")
        candidate = thaw_json(freeze_json(raw))
        errors = sorted(
            self._validator.iter_errors(candidate),  # type: ignore[union-attr]
            key=lambda error: (
                tuple(str(part) for part in error.absolute_path),
                str(error.validator),
            ),
        )[:20]
        if errors:
            details = tuple(
                ToolArgumentIssue(
                    field=".".join(str(part) for part in error.absolute_path),
                    message=_json_schema_message(str(error.validator)),
                    type=f"json_schema_{error.validator}",
                )
                for error in errors
            )
            raise ToolArgumentValidationError(details)
        frozen = freeze_json(candidate)
        if not isinstance(frozen, Mapping):
            raise _json_schema_validation_error("", "工具参数必须是 JSON object", "type")
        return frozen

    def to_mapping(self, value: FrozenJsonObject) -> FrozenJsonObject:
        frozen = freeze_json(value)
        if not isinstance(frozen, Mapping):
            raise TypeError("工具参数必须转换为 JSON object")
        return frozen


def _reject_external_references(value: FrozenJson) -> None:
    if isinstance(value, Mapping):
        reference = value.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#"):
            raise ValueError("JSON Schema 不允许外部 $ref")
        for item in value.values():
            _reject_external_references(item)
    elif isinstance(value, tuple):
        for item in value:
            _reject_external_references(item)


def _json_schema_validation_error(
    field_name: str, message: str, error_type: str
) -> ToolArgumentValidationError:
    return ToolArgumentValidationError((ToolArgumentIssue(field_name, message, error_type),))


def _json_schema_message(validator_name: str) -> str:
    messages = {
        "additionalProperties": "不允许额外字段",
        "enum": "值不在允许范围内",
        "format": "格式不符合要求",
        "maximum": "值超过最大值",
        "maxLength": "长度超过上限",
        "minimum": "值低于最小值",
        "minLength": "长度低于下限",
        "pattern": "格式不符合要求",
        "required": "缺少必填字段",
        "type": "类型不匹配",
    }
    return messages.get(validator_name, "不符合 JSON Schema 约束")
