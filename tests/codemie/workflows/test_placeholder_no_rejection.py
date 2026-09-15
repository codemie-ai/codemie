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

from unittest.mock import MagicMock, patch

from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.workflows.workflow import WorkflowExecutor

PLACEHOLDER_YAML_CONFIG = """
assistants:
  - id: business_analyst
    assistant_id: 196ede41-e7f0-4658-ae99-1dc0d83c8347
    model: 'gpt-4o-2024-11-20'
states:
  - id: business_analyst
    assistant_id: business_analyst
    task: |
      Keep literal ${input:assistant_id} text.
    output_schema: |
      {
        "success": "Boolean true | false"
      }
    next:
      condition:
        expression: "success == True"
        then: end
        otherwise: business_analyst
"""


def test_validate_workflow_accepts_placeholder_tokens_as_plain_text():
    """Persisted workflows treat ${input:...} as literal text — no rejection."""
    wf = WorkflowConfig(
        name="WF",
        description="Team ${input:team_name}",
        yaml_config=PLACEHOLDER_YAML_CONFIG,
        mode="Sequential",
    )
    user = MagicMock()
    user.project_names = ["app"]

    with (
        patch("codemie.workflows.workflow.validate_workflow_config_resources_availability"),
        patch.object(
            WorkflowExecutor,
            "create_executor",
            MagicMock(return_value=MagicMock(_init_workflow=MagicMock(return_value=None))),
        ),
    ):
        # Must not raise — placeholder token is literal text in persisted workflows
        WorkflowExecutor.validate_workflow(wf, user, error_format="string")
