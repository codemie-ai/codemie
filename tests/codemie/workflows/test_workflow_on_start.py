# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

import pytest
from unittest.mock import MagicMock, patch

from codemie.workflows.constants import CONTEXT_STORE_VARIABLE, PROPAGATED_HEADERS_CONTEXT_KEY
from codemie.workflows.workflow import WorkflowExecutor, _SENSITIVE_HEADER_NAMES


def _make_on_start_executor(
    request_headers: dict | None = None,
    resume_execution: bool = False,
    execution_id: str | None = None,
) -> MagicMock:
    """Build a minimal MagicMock that stands in for WorkflowExecutor self."""
    executor = MagicMock(spec=WorkflowExecutor)
    executor.user_input = ""  # empty → initial_context={} without parse call
    executor.file_names = None  # prevent file_names branch from running
    executor.request_headers = request_headers
    executor.resume_execution = resume_execution
    executor.execution_id = execution_id
    executor.workflow_config = MagicMock()
    executor.workflow_execution_service = MagicMock()
    executor.user = MagicMock()
    return executor


@patch("codemie.workflows.workflow.WorkflowService")
def test_on_workflow_start_injects_headers_when_present(mock_ws):
    executor = _make_on_start_executor(request_headers={"X-Tenant": "acme"})

    inputs = WorkflowExecutor.on_workflow_start(executor)

    assert inputs is not None
    assert inputs[CONTEXT_STORE_VARIABLE][PROPAGATED_HEADERS_CONTEXT_KEY] == {"X-Tenant": "acme"}


@pytest.mark.parametrize("headers", [None, {}])
@patch("codemie.workflows.workflow.WorkflowService")
def test_on_workflow_start_omits_headers_key_when_falsy(mock_ws, headers):
    executor = _make_on_start_executor(request_headers=headers)

    inputs = WorkflowExecutor.on_workflow_start(executor)

    assert inputs is not None
    assert PROPAGATED_HEADERS_CONTEXT_KEY not in inputs[CONTEXT_STORE_VARIABLE]


@patch("codemie.workflows.workflow.WorkflowService")
def test_on_workflow_start_redacts_sensitive_headers(mock_ws):
    headers = {"X-Tenant": "acme", "Authorization": "Bearer secret", "x-api-key": "key123"}
    executor = _make_on_start_executor(request_headers=headers)

    inputs = WorkflowExecutor.on_workflow_start(executor)

    assert inputs is not None
    propagated = inputs[CONTEXT_STORE_VARIABLE][PROPAGATED_HEADERS_CONTEXT_KEY]
    assert propagated == {"X-Tenant": "acme"}
    assert "Authorization" not in propagated
    assert "x-api-key" not in propagated


@patch("codemie.workflows.workflow.WorkflowService")
def test_on_workflow_start_omits_key_when_all_headers_sensitive(mock_ws):
    headers = dict.fromkeys(_SENSITIVE_HEADER_NAMES, "value")
    executor = _make_on_start_executor(request_headers=headers)

    inputs = WorkflowExecutor.on_workflow_start(executor)

    assert inputs is not None
    assert PROPAGATED_HEADERS_CONTEXT_KEY not in inputs[CONTEXT_STORE_VARIABLE]


@patch("codemie.workflows.workflow.WorkflowService")
def test_on_workflow_start_resume_returns_none_inputs(mock_ws):
    executor = _make_on_start_executor(
        request_headers={"X-Foo": "bar"},
        resume_execution=True,
        execution_id="exec-123",
    )

    inputs = WorkflowExecutor.on_workflow_start(executor)

    assert inputs is None


@pytest.mark.parametrize(
    "raw_value, expected_value",
    [
        ("normal-value", "normal-value"),
        ("val\r\nX-Injected: evil", "valX-Injected: evil"),
        ("val\ninjected", "valinjected"),
        ("val\rinjected", "valinjected"),
        ("\r\n", ""),
        ("multi\r\nline\r\nvalue", "multilinevalue"),
    ],
)
@patch("codemie.workflows.workflow.WorkflowService")
def test_on_workflow_start_strips_crlf_from_header_values(mock_ws, raw_value, expected_value):
    executor = _make_on_start_executor(request_headers={"X-Custom": raw_value})

    inputs = WorkflowExecutor.on_workflow_start(executor)

    assert inputs is not None
    propagated = inputs[CONTEXT_STORE_VARIABLE][PROPAGATED_HEADERS_CONTEXT_KEY]
    assert propagated["X-Custom"] == expected_value
