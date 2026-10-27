# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from typing import Annotated, Any, ClassVar, ForwardRef, Union
from unittest.mock import Mock, patch

import pytest
from codemie_tools.ms_exchange.tools import ReadInput
from langchain_core.tools import BaseTool
from langchain_core.utils.pydantic import TypeBaseModel
from pydantic import BaseModel, Field, model_validator
from pydantic.v1 import BaseModel as BaseModelV1

from codemie.service.tools import tool_execution_service
from codemie.service.tools.tool_execution_service import (
    ToolExecutionService,
)


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (int, "int"),
        ("int", "int"),
        (list[int], "list[int]"),
        ("list[int]", "list[int]"),
        (dict[str, list[int]], "dict[str, list[int]]"),
        (int | None, "int | None"),
        (str | int, "str | int"),
        (Union[int, None], "typing.Optional[int]"),
        ("SomeInput", "SomeInput"),
        (ForwardRef("NotDefinedAnywhere"), "NotDefinedAnywhere"),
        (Annotated[int, "units"], "int"),
        (Annotated[list[int], "units"], "list[int]"),
        (list[Annotated[int, "units"]], "list[typing.Annotated[int, 'units']]"),
    ],
)
def test_format_annotation_preserves_type_information(annotation: Any, expected: str) -> None:
    assert callable(getattr(tool_execution_service, "_format_annotation", None))
    assert tool_execution_service._format_annotation(annotation) == expected


@pytest.mark.parametrize("base", [BaseModel, BaseModelV1], ids=["v2", "v1"])
def test_build_signature_discovers_inherited_fields_and_child_overrides(base: TypeBaseModel) -> None:
    class Parent(base):
        inherited: int = 7
        overridden: int = 1

    class Child(Parent):
        overridden: str = "child"
        items: list[int]
        amount: Annotated[int, "units"]
        marker: ClassVar[str] = "internal"

    assert callable(getattr(tool_execution_service, "_build_signature", None))
    assert tool_execution_service._build_signature(Child) == {
        "inherited": "int",
        "overridden": "str",
        "items": "list[int]",
        "amount": "int",
    }


@pytest.mark.parametrize("base", [BaseModel, BaseModelV1], ids=["v2", "v1"])
def test_build_signature_keeps_unresolved_forward_reference(base: TypeBaseModel) -> None:
    class UnresolvedInput(base):
        value: NotDefinedAnywhere  # noqa: F821

    assert callable(getattr(tool_execution_service, "_build_signature", None))
    assert tool_execution_service._build_signature(UnresolvedInput) == {"value": "NotDefinedAnywhere"}


def test_validate_tool_args_v1_constructor_typeerror_has_expected_valueerror() -> None:
    class TypeErrorInput(BaseModelV1):
        items: list[int]

        def __init__(self, **data: Any) -> None:
            raise TypeError("constructor rejected arguments")

    class V1SchemaTool(ValidationTool):
        args_schema: TypeBaseModel = TypeErrorInput

    tool = V1SchemaTool()
    with pytest.raises(Exception) as exc:
        ToolExecutionService.validate_tool_args(tool, {"items": [1]})

    assert type(exc.value) is ValueError
    assert str(exc.value) == "Tool method arguments are not correct. Expected: {'items': 'list[int]'}"


def test_validate_tool_attributes_signature_includes_inheritance_and_overrides() -> None:
    class ChildTool(ValidationTool):
        custom_count: str = "child"
        custom_mapping: Annotated[dict[str, list[int]], "units"] = Field(default_factory=dict)
        marker: ClassVar[str] = "internal"

    with pytest.raises(Exception) as exc:
        ToolExecutionService.validate_tool_attributes(ChildTool(), {"custom_count": []})

    assert type(exc.value) is ValueError
    message = str(exc.value)
    assert message.startswith("Tool attributes are not correct. Expected:")
    assert message.count("'custom_count': 'str'") == 1
    assert "'custom_union': 'str | int'" in message
    assert "'custom_mapping': 'dict[str, list[int]]'" in message
    assert "'marker'" not in message


def test_validate_tool_args_accepts_omitted_exchange_read_defaults() -> None:
    tool = Mock(spec=BaseTool)
    tool.args_schema = ReadInput
    tool_args = {}

    assert ToolExecutionService.validate_tool_args(tool, tool_args) is tool
    assert tool_args == {}


def test_validate_tool_args_valid():
    """Test validating tool args with valid arguments."""
    # Setup mock
    tool = Mock(spec=BaseTool)
    schema_mock = Mock()
    schema_mock.__annotations__ = {'arg1': str, 'arg2': int}
    tool.args_schema = schema_mock

    # Valid args
    tool_args = {"arg1": "test", "arg2": 123}

    # Validate with patch to avoid calling the actual schema constructor
    with patch.object(tool.args_schema, '__call__', return_value=None):  # Mock successful validation
        result = ToolExecutionService.validate_tool_args(tool, tool_args)

    # Assert
    assert result == tool


class InheritedInput(BaseModel):
    inherited_count: int = 7


class ValidationInput(InheritedInput):
    required: int
    required_nullable: str | None
    count: int = 10
    nullable: str | None = "fallback"
    items: list[int] = Field(default_factory=list)
    observed: ClassVar[list[dict]] = []

    @model_validator(mode="after")
    def observe_validated_arguments(self):
        self.observed.append(self.model_dump())
        return self


class ValidationTool(BaseTool):
    name: str = "validation_test"
    description: str = "Real tool validation regression"
    args_schema: type[BaseModel] = ValidationInput
    custom_count: int = 3
    custom_union: str | int = "value"
    custom_items: list[int] = Field(default_factory=list)

    def _run(self, **kwargs):
        return ""


@pytest.fixture
def real_tool():
    ValidationInput.observed.clear()
    return ValidationTool()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, {"inherited_count": 7, "count": 10, "nullable": "fallback", "items": []}),
        (
            {"count": 10, "items": []},
            {"inherited_count": 7, "count": 10, "nullable": "fallback", "items": []},
        ),
        ({"nullable": None}, {"inherited_count": 7, "count": 10, "nullable": None, "items": []}),
        (
            {"inherited_count": 2, "count": 4, "nullable": "provided", "items": [1]},
            {"inherited_count": 2, "count": 4, "nullable": "provided", "items": [1]},
        ),
    ],
)
def test_validate_tool_args_preserves_defaults_and_supplied_values(real_tool, overrides, expected):
    tool_args = {"required": 1, "required_nullable": None, **overrides}
    original = tool_args.copy()

    assert ToolExecutionService.validate_tool_args(real_tool, tool_args) is real_tool
    assert ValidationInput.observed == [{"required": 1, "required_nullable": None, **expected}]
    assert tool_args == original


@pytest.mark.parametrize(
    "tool_args",
    [
        {"required_nullable": None},
        {"required": 1},
        {"required": 1, "count": 10, "nullable": "fallback", "items": []},
        {"required": None, "required_nullable": None},
        {"required": "invalid", "required_nullable": None},
        {"required": 1, "required_nullable": None, "count": None},
        {"required": 1, "required_nullable": None, "items": ["invalid"]},
    ],
)
def test_validate_tool_args_rejects_invalid_arguments_with_resolved_signature(real_tool, tool_args):
    original = tool_args.copy()
    with pytest.raises(ValueError, match="Tool method arguments are not correct. Expected:") as exc:
        ToolExecutionService.validate_tool_args(real_tool, tool_args)

    message = str(exc.value)
    assert "'required': 'int'" in message
    assert "'required_nullable': 'str | None'" in message
    assert "'inherited_count': 'int'" in message
    assert "'items': 'list[int]'" in message
    assert tool_args == original


@pytest.mark.parametrize(
    "tool_attributes",
    [{"custom_count": None}, {"custom_count": "invalid"}, {"custom_union": []}, {"custom_items": ["invalid"]}],
)
def test_validate_tool_attributes_rejects_invalid_values_with_resolved_signature(real_tool, tool_attributes):
    original = tool_attributes.copy()
    with pytest.raises(ValueError, match="Tool attributes are not correct. Expected:") as exc:
        ToolExecutionService.validate_tool_attributes(real_tool, tool_attributes)

    message = str(exc.value)
    assert "'name': 'str'" in message
    assert "'custom_count': 'int'" in message
    assert "'custom_union': 'str | int'" in message
    assert "'custom_items': 'list[int]'" in message
    assert tool_attributes == original
    assert real_tool.custom_count == 3


def test_validate_tool_attributes_returns_validated_tool_without_mutating_inputs(real_tool):
    tool_attributes = {"custom_count": 5}

    result = ToolExecutionService.validate_tool_attributes(real_tool, tool_attributes)

    assert isinstance(result, ValidationTool)
    assert result is not real_tool
    assert result.custom_count == 5
    assert real_tool.custom_count == 3
    assert tool_attributes == {"custom_count": 5}


def test_update_tool_attributes_preserves_filtering_and_original_values(real_tool):
    tool_attributes = {"custom_count": "5", "unknown": "ignored"}

    with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
        assert ToolExecutionService.update_tool_attributes(real_tool, tool_attributes) is real_tool
    assert real_tool.custom_count == "5"
    assert not hasattr(real_tool, "unknown")
    assert tool_attributes == {"custom_count": "5", "unknown": "ignored"}


def test_validate_tool_attributes_valid():
    """Test validating tool attributes with valid attributes."""
    # Setup mock
    tool = Mock(spec=BaseTool)
    tool.__annotations__ = {"name": str, "description": str, "custom_attr": str}
    tool.model_validate = Mock(return_value=tool)

    # Valid tool attributes
    tool_attributes = {"custom_attr": "new value"}

    # Validate
    result = ToolExecutionService.validate_tool_attributes(tool, tool_attributes)

    # Assert the tool was validated
    tool.model_validate.assert_called_once()
    assert result == tool
