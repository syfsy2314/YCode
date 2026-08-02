import pytest
from pydantic import BaseModel, ConfigDict, Field

from ycode.core.messages import thaw_json
from ycode.tools.arguments import (
    JsonSchemaToolArguments,
    PydanticToolArguments,
    ToolArguments,
    ToolArgumentValidationError,
)


class ExampleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    count: int = Field(default=1, ge=1)


def test_pydantic_adapter_exposes_schema_and_field_names() -> None:
    arguments = PydanticToolArguments(ExampleArguments)

    assert isinstance(arguments, ToolArguments)
    assert arguments.field_names == frozenset({"path", "count"})
    assert thaw_json(arguments.input_schema)["properties"]["count"]["minimum"] == 1


def test_pydantic_adapter_validates_and_converts_to_mapping() -> None:
    arguments = PydanticToolArguments(ExampleArguments)

    validated = arguments.validate({"path": "a.txt"})

    assert validated == ExampleArguments(path="a.txt", count=1)
    assert thaw_json(arguments.to_mapping(validated)) == {"path": "a.txt", "count": 1}


def test_pydantic_adapter_converts_errors_without_raw_values() -> None:
    arguments = PydanticToolArguments(ExampleArguments)

    with pytest.raises(ToolArgumentValidationError) as caught:
        arguments.validate({"path": "", "extra": "secret-value"})

    error = caught.value
    assert [detail.field for detail in error.details] == ["extra", "path"]
    assert "secret-value" not in repr(error.details)
    assert all(detail.type for detail in error.details)


def test_json_schema_adapter_validates_local_references() -> None:
    arguments = JsonSchemaToolArguments(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"path": {"$ref": "#/$defs/path"}},
            "required": ["path"],
            "$defs": {"path": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        }
    )

    validated = arguments.validate({"path": "file.txt"})

    assert arguments.field_names == frozenset({"path"})
    assert thaw_json(validated) == {"path": "file.txt"}


def test_json_schema_adapter_rejects_external_reference() -> None:
    with pytest.raises(ValueError, match="外部"):
        JsonSchemaToolArguments({"type": "object", "$ref": "https://example.test/schema"})


def test_json_schema_adapter_returns_safe_sorted_and_bounded_errors() -> None:
    arguments = JsonSchemaToolArguments(
        {
            "type": "object",
            "properties": {f"field_{index}": {"type": "integer"} for index in range(25)},
            "additionalProperties": False,
        }
    )

    with pytest.raises(ToolArgumentValidationError) as caught:
        arguments.validate({f"field_{index}": "secret-value" for index in range(25)})

    assert len(caught.value.details) == 20
    assert [detail.field for detail in caught.value.details] == sorted(
        detail.field for detail in caught.value.details
    )
    assert "secret-value" not in repr(caught.value.details)
