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

"""Invalid arguments for Exchange write tools are rejected with a readable, value-free message.

The Exchange schemas live in a module with ``from __future__ import annotations``, so ``__annotations__`` holds
strings. ``ToolNode`` used to call ``.__name__`` on them and replaced the validation diagnosis with
``'str' object has no attribute '__name__'``.
"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from codemie.core.workflow_models import WorkflowConfig, WorkflowNextState, WorkflowState, WorkflowTool
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE, MESSAGES_VARIABLE
from codemie.workflows.nodes.tool_node import ToolNode
from codemie_tools.ms_exchange.tools import DeleteInput, ForwardInput, MoveInput, SendInput, TagInput

SECRET = "SECRET-INPUT-VALUE"

CASES = [
    pytest.param(SendInput, {"to": SECRET, "subject": "s", "body": "b"}, "to", id="send-to-wrong-type"),
    pytest.param(SendInput, {"to": ["a@b.c"], "body": SECRET}, "subject", id="send-missing-subject"),
    pytest.param(ForwardInput, {"message_id": SECRET}, "to", id="forward-missing-to"),
    pytest.param(MoveInput, {"message_id": SECRET}, "target_folder", id="move-missing-target-folder"),
    pytest.param(TagInput, {"message_id": SECRET}, "category", id="tag-missing-category"),
    pytest.param(DeleteInput, {"message_id": "m", "soft": SECRET}, "soft", id="delete-soft-wrong-type"),
]


def _node(tool_args: dict) -> ToolNode:
    config = MagicMock(spec=WorkflowConfig)
    config.tools = [WorkflowTool(id="tool_1", tool="exchange_tool", tool_args=tool_args)]
    return ToolNode(
        callbacks=[MagicMock()],
        workflow_execution_service=MagicMock(),
        thought_queue=MagicMock(),
        workflow_state=WorkflowState(
            id="tool_node",
            task="Run tool",
            next=WorkflowNextState(state_id="next"),
            tool_id="tool_1",
            tool_args=tool_args,
        ),
        workflow_config=config,
        user=MagicMock(),
        execution_id="exec_123",
    )


@pytest.mark.parametrize("schema, tool_args, bad_field", CASES)
def test_exchange_invalid_args_are_rejected_with_field_hint(schema, tool_args, bad_field):
    tool = MagicMock()
    tool.args_schema = schema
    state_schema = {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}

    with patch("codemie.workflows.nodes.tool_node.logger") as mock_logger:
        with pytest.raises(ValueError) as exc_info:
            _node(tool_args)._execute_tool_with_args(tool, state_schema)

    message = str(exc_info.value)
    assert message.startswith("Tool method arguments are not correct. Expected:")
    assert "__name__" not in message
    assert f"'{bad_field}'" in message
    assert SECRET not in message
    tool.execute.assert_not_called()

    # The weak `SECRET not in message` check above already passed before this value-free
    # logging was added, so verify the log itself never carries the submitted value.
    logged = " ".join(str(call) for call in mock_logger.error.call_args_list)
    assert SECRET not in logged


def test_exchange_expected_signature_describes_resolved_types():
    tool = MagicMock()
    tool.args_schema = SendInput
    state_schema = {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}

    with pytest.raises(ValueError) as exc_info:
        _node({"to": SECRET, "subject": "s", "body": "b"})._execute_tool_with_args(tool, state_schema)

    message = str(exc_info.value)
    assert "'to': 'array of string (required)'" in message
    assert "'subject': 'string (required)'" in message
    assert "'body_type': 'string'" in message


def test_execute_type_error_propagates_unmodified():
    """A bug inside the tool's own execute (not a signature mismatch) must not be
    misreported as an invalid-signature error -- it must propagate with its own
    message and traceback so it stays diagnosable."""

    class _BuggyTool:
        args_schema = SendInput

        @staticmethod
        def execute(to, subject, body, body_type="text"):
            raise TypeError("boom: internal tool bug, not a signature mismatch")

    state_schema = {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}

    with pytest.raises(TypeError, match="boom: internal tool bug"):
        _node({"to": ["a@b.c"], "subject": "s", "body": "b"})._execute_tool_with_args(_BuggyTool(), state_schema)


def test_unexpected_keyword_argument_still_yields_signature_error():
    """A tool_input key that validates fine against the args schema but that `execute`
    does not accept must still be reported as an invalid-signature error -- matching the
    historical call-time-TypeError behaviour -- and `execute` must never be called."""

    class _ArgsModel(BaseModel):
        value: str
        extra: str = "unused"

    class _MismatchedTool:
        args_schema = _ArgsModel

        def __init__(self):
            self.executed = False

        def execute(self, value):
            self.executed = True
            return value

    tool = _MismatchedTool()
    state_schema = {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}

    with pytest.raises(ValueError, match="Tool method arguments are not correct"):
        _node({"value": "a", "extra": SECRET})._execute_tool_with_args(tool, state_schema)

    assert tool.executed is False


def test_validation_and_signature_logs_never_contain_submitted_values():
    """Neither the ValidationError branch nor the TypeError/bind-mismatch branch may ever
    log a submitted value -- only field locations/error types or parameter names."""

    with patch("codemie.workflows.nodes.tool_node.logger") as mock_logger:
        with pytest.raises(ValueError):
            _node({"to": SECRET, "subject": "s", "body": "b"})._execute_tool_with_args(
                MagicMock(args_schema=SendInput), {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}
            )
    logged = " ".join(str(call) for call in mock_logger.error.call_args_list)
    assert SECRET not in logged

    class _ArgsModel(BaseModel):
        value: str
        extra: str = "unused"

    class _MismatchedTool:
        args_schema = _ArgsModel

        def execute(self, value):
            return value

    with patch("codemie.workflows.nodes.tool_node.logger") as mock_logger:
        with pytest.raises(ValueError):
            _node({"value": "a", "extra": SECRET})._execute_tool_with_args(
                _MismatchedTool(), {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}
            )
    logged = " ".join(str(call) for call in mock_logger.error.call_args_list)
    assert SECRET not in logged


def test_dict_schema_tool_describes_ref_anyof_array_and_list_types():
    """MCP-style (JSON Schema / dict) tools get the same readable, value-free
    description as pydantic-schema tools, including $ref, anyOf (Optional),
    array-of-items and list-type labels."""

    properties = {
        "ref_field": {"$ref": "#/$defs/Foo"},
        "opt_field": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "arr_field": {"type": "array", "items": {"type": "integer"}},
        "list_type_field": {"type": ["string", "integer"]},
        "plain_field": {"type": "string"},
    }
    json_schema = {"properties": properties, "required": ["ref_field", "plain_field"]}

    class _FailingArgsModel:
        def __init__(self, **kwargs):
            raise TypeError("simulated MCP schema validation failure")

    tool = MagicMock()
    tool.args_schema = json_schema
    tool.model_args.return_value = _FailingArgsModel

    with pytest.raises(ValueError) as exc_info:
        _node({"plain_field": "x"})._execute_tool_with_args(tool, {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []})

    message = str(exc_info.value)
    assert "'ref_field': 'Foo (required)'" in message
    assert "'opt_field': 'string or null'" in message
    assert "'arr_field': 'array of integer'" in message
    assert "'list_type_field': 'string or integer'" in message
    assert "'plain_field': 'string (required)'" in message
    tool.execute.assert_not_called()


def test_annotation_only_schema_falls_back_to_name_label():
    """When the args schema has no model_json_schema and no JSON-schema properties,
    the expected-type description falls back to the annotation's `__name__`."""

    class _PlainArgs:
        __annotations__ = {"value": str}

    tool = MagicMock()
    tool.args_schema = _PlainArgs

    with pytest.raises(ValueError) as exc_info:
        _node({"value": "x"})._execute_tool_with_args(tool, {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []})

    assert "'value': 'str'" in str(exc_info.value)
    tool.execute.assert_not_called()
