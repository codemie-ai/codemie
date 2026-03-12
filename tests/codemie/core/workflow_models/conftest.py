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

import pytest

from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowState,
)


@pytest.fixture
def workflow_config():
    return WorkflowConfig(
        id="workflow_123",
        name="Test Workflow",
        description="A test workflow",
        states=[
            WorkflowState(id="state1", assistant_id="assistant1", task="task1", next={"state_id": "state2"}),
            WorkflowState(id="state2", assistant_id="assistant2", task="task2", next={"state_id": "end"}),
        ],
    )
