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

"""Offline corpus harness: every built-in workflow template passes the real save-time validation."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from codemie.configs.config import config
from codemie.core.workflow_models.workflow_config import WorkflowConfig, WorkflowConfigTemplate
from codemie.workflows.workflow import WorkflowExecutor

WORKFLOW_TEMPLATES_DIR = config.WORKFLOW_TEMPLATES_DIR
TEMPLATE_FILES = sorted(Path(WORKFLOW_TEMPLATES_DIR).glob("*_template.yaml"))
MIN_TEMPLATE_COUNT = 64


def _load_template(path: Path) -> WorkflowConfigTemplate | None:
    """Load a template through the production loader, which keeps ${input:...} placeholders as literal text."""
    return WorkflowConfigTemplate.from_yaml(path.read_text(encoding="utf-8"))


def test_template_corpus_is_complete():
    """Guard against a vacuous pass: enough templates exist and each has the expected shape."""
    assert len(TEMPLATE_FILES) >= MIN_TEMPLATE_COUNT

    offenders = []
    for path in TEMPLATE_FILES:
        template = _load_template(path)
        execution_config = yaml.safe_load(template.yaml_config) if template else None
        if not isinstance(execution_config, dict):
            offenders.append(path.name)

    assert not offenders, f"Templates without a mapping 'execution_config': {offenders}"


@pytest.mark.parametrize("template_path", TEMPLATE_FILES, ids=[p.name for p in TEMPLATE_FILES])
def test_template_saves_without_error(template_path: Path):
    """The template passes WorkflowExecutor.validate_workflow, as it would when saved by a user."""
    template = _load_template(template_path)
    assert template is not None, f"{template_path.name} failed to load"
    workflow_config = WorkflowConfig(
        name=template.name,
        description=template.description,
        mode=template.mode,
        yaml_config=template.yaml_config,
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
        WorkflowExecutor.validate_workflow(workflow_config, user, error_format="string")
