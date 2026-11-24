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
"""WorkflowExecutor.initialize_assistant (supervisor path) forwards the workflow Headroom override."""

from unittest.mock import Mock, patch

from codemie.core.workflow_models import WorkflowAssistant, WorkflowConfig
from codemie.service.headroom.models import HeadroomOverride
from codemie.workflows.workflow import WorkflowExecutor


def _executor(workflow_config):
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_config = workflow_config
    executor.user_input = "hi"
    executor.user = Mock()
    executor.file_names = []
    executor.thought_queue = Mock()
    executor.resume_execution = False
    executor.execution_id = "exec-1"
    executor.request_headers = None
    executor.disable_cache = False
    return executor


def _options(workflow_config):
    with patch("codemie.workflows.workflow.initialize_assistant") as initialize:
        _executor(workflow_config).initialize_assistant(WorkflowAssistant(id="a"))
    return initialize.call_args.kwargs["options"]


def test_workflow_override_is_forwarded():
    override = HeadroomOverride(enabled=True)

    options = _options(WorkflowConfig(name="n", description="d", project="p", headroom_config=override))

    assert options.headroom_workflow == override


def test_absent_or_non_override_is_none():
    assert _options(WorkflowConfig(name="n", description="d", project="p")).headroom_workflow is None

    mock_config = Mock(spec=WorkflowConfig)
    mock_config.project = "p"
    mock_config.is_global = False
    mock_config.headroom_config = Mock()
    assert _options(mock_config).headroom_workflow is None
