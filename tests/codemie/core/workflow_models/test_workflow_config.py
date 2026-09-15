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

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.configs import config
from codemie.core.exceptions import NotFoundException
from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowRetryPolicy,
    YamlConfigHistory,
    RETRY_POLICY_DEFAULT_BACKOFF_FACTOR,
    RETRY_POLICY_DEFAULT_MAX_INTERVAL,
    RETRY_POLICY_DEFAULT_MAX_ATTEMPTS,
    RETRY_POLICY_DEFAULT_INITIAL_INTERVAL,
)
from codemie.rest_api.models.base import BaseModelWithSQLSupport
from codemie.rest_api.security.user import User
from codemie.core.workflow_models.workflow_config import WorkflowConfigListResponse


class TestWorkflowConfig:
    @pytest.fixture
    def mock_user(self):
        return User(
            id="user",
            project_names=["app"],
            admin_project_names=["app"],
        )

    @pytest.fixture
    def mock_non_admin_user(self):
        mock_user = MagicMock()
        mock_user.is_admin = False
        return mock_user

    def test_parse_yaml_config(self):
        yaml_data = """
        name: Sequential Workflow Example
        description: Example of sequential workflow
        start_hint: Start by describing your goal
        mode: Sequential

        execution_config:
            assistants:
              - id: "assistant_1"
                assistant_id: "assistant_1"
                name: "Test Assistant"
                description: "This is a test assistant."
                model: "test_model"
        """
        result = WorkflowConfig.from_yaml(yaml_data)
        assert isinstance(result, WorkflowConfig)
        assert result.start_hint == "Start by describing your goal"
        assert len(result.assistants) == 1
        assert result.assistants[0].id == "assistant_1"

    def test_start_hint_property(self):
        """Test that start_hint property can be set and retrieved."""
        wf = WorkflowConfig(
            id="wf-test",
            name="Test Workflow",
            description="Test description",
            start_hint="This is a helpful hint to get started",
        )
        assert wf.start_hint == "This is a helpful hint to get started"

    def test_start_hint_optional_none(self):
        """Test that start_hint can be None."""
        wf = WorkflowConfig(
            id="wf-test-2",
            name="Test Workflow 2",
            description="Test description without hint",
            start_hint=None,
        )
        assert wf.start_hint is None

    def test_start_hint_in_model_dump(self):
        """Test that start_hint is included in model dump."""
        wf = WorkflowConfig(
            id="wf-test-3",
            name="Test Workflow 3",
            description="Test description",
            start_hint="Start by analyzing the requirements",
        )
        dumped = wf.model_dump()
        assert dumped["start_hint"] == "Start by analyzing the requirements"

    def test_get_effective_retry_policy(self, workflow_config):
        # Set up a workflow config with a default retry policy
        default_retry_policy = WorkflowRetryPolicy(
            max_attempts=3, initial_interval=1.0, backoff_factor=2.0, max_interval=60.0
        )
        workflow_config.retry_policy = default_retry_policy

        # Define a specific retry policy for a state
        specific_state_retry_policy = WorkflowRetryPolicy(
            max_attempts=5, initial_interval=0.5, backoff_factor=2.5, max_interval=30.0
        )
        workflow_config.states[0].retry_policy = specific_state_retry_policy

        # Test getting effective retry policy for a state with a specific policy
        state_retry_policy = workflow_config.get_effective_retry_policy(workflow_config.states[0])
        assert state_retry_policy.initial_interval == specific_state_retry_policy.initial_interval
        assert state_retry_policy.backoff_factor == specific_state_retry_policy.backoff_factor
        assert state_retry_policy.max_interval == specific_state_retry_policy.max_interval
        assert state_retry_policy.max_attempts == specific_state_retry_policy.max_attempts

        # Test getting effective retry policy for a state without a specific policy
        no_policy_state_retry_policy = workflow_config.get_effective_retry_policy(workflow_config.states[1])
        assert no_policy_state_retry_policy.initial_interval == default_retry_policy.initial_interval
        assert no_policy_state_retry_policy.backoff_factor == default_retry_policy.backoff_factor
        assert no_policy_state_retry_policy.max_interval == default_retry_policy.max_interval
        assert no_policy_state_retry_policy.max_attempts == default_retry_policy.max_attempts

        # Test getting effective retry policy when no policy is defined at both levels
        workflow_config.retry_policy = WorkflowRetryPolicy()
        workflow_config.states[0].retry_policy = WorkflowRetryPolicy()
        fallback_retry_policy = workflow_config.get_effective_retry_policy(workflow_config.states[0])
        assert fallback_retry_policy.initial_interval == RETRY_POLICY_DEFAULT_INITIAL_INTERVAL
        assert fallback_retry_policy.backoff_factor == RETRY_POLICY_DEFAULT_BACKOFF_FACTOR
        assert fallback_retry_policy.max_interval == RETRY_POLICY_DEFAULT_MAX_INTERVAL
        assert fallback_retry_policy.max_attempts == RETRY_POLICY_DEFAULT_MAX_ATTEMPTS

    def test_get_max_concurrency(self, workflow_config):
        workflow_config.max_concurrency = None
        assert workflow_config.get_max_concurrency() == config.WORKFLOW_DEFAULT_CONCURRENCY

        workflow_config.max_concurrency = config.WORKFLOW_MAX_CONCURRENCY
        assert workflow_config.get_max_concurrency() == config.WORKFLOW_MAX_CONCURRENCY

        workflow_config.max_concurrency = 0
        assert workflow_config.get_max_concurrency() == 1

        workflow_config.max_concurrency = config.WORKFLOW_MAX_CONCURRENCY + 1
        assert workflow_config.get_max_concurrency() == config.WORKFLOW_MAX_CONCURRENCY


class TestWorkflowConfigListResponse:
    def test_start_hint_is_present_in_dump(self):
        """Test that start_hint property is included in model dump."""
        item = WorkflowConfigListResponse(
            id="wf-1",
            name="Test Workflow",
            description="Test workflow description",
            start_hint="Try: summarize the repo and suggest next steps",
            project="app",
            shared=True,
            mode="Sequential",
        )
        dumped = item.model_dump()
        assert dumped["start_hint"] == "Try: summarize the repo and suggest next steps"

    def test_start_hint_optional(self):
        """Test that start_hint is optional and can be None."""
        item = WorkflowConfigListResponse(
            id="wf-2",
            name="Test Workflow 2",
            description="Test workflow without start hint",
            start_hint=None,
            project="app",
            shared=False,
            mode="Autonomous",
        )
        dumped = item.model_dump()
        assert dumped["start_hint"] is None


class TestParseExecutionConfigSkillIds:
    """Tests that parse_execution_config correctly handles skill_ids from YAML,
    including the edge case where YAML has 'skill_ids:' with no value (null)."""

    def test_parse_execution_config_null_skill_ids_coerced_to_empty_list(self):
        """YAML with 'skill_ids:' (null value) must produce skill_ids=[] not None."""
        import yaml

        yaml_config = yaml.dump(
            {
                "assistants": [{"id": "a1", "model": "gpt-4.1", "skill_ids": None}],
                "states": [],
            }
        )
        wf = WorkflowConfig(id="wf1", name="WF", description="", yaml_config=yaml_config)
        wf.parse_execution_config()

        assert len(wf.assistants) == 1
        assert (
            wf.assistants[0].skill_ids == []
        ), "skill_ids must be [] not None when YAML has 'skill_ids:' with no value"

    def test_parse_execution_config_populated_skill_ids_preserved(self):
        """YAML with actual skill IDs must populate skill_ids correctly."""
        import yaml

        yaml_config = yaml.dump(
            {
                "assistants": [{"id": "a1", "model": "gpt-4.1", "skill_ids": ["skill-abc"]}],
                "states": [],
            }
        )
        wf = WorkflowConfig(id="wf2", name="WF", description="", yaml_config=yaml_config)
        wf.parse_execution_config()

        assert len(wf.assistants) == 1
        assert wf.assistants[0].skill_ids == ["skill-abc"]

    def test_parse_execution_config_missing_skill_ids_defaults_to_empty_list(self):
        """YAML assistant without skill_ids field must default to []."""
        import yaml

        yaml_config = yaml.dump(
            {
                "assistants": [{"id": "a1", "model": "gpt-4.1"}],
                "states": [],
            }
        )
        wf = WorkflowConfig(id="wf3", name="WF", description="", yaml_config=yaml_config)
        wf.parse_execution_config()

        assert len(wf.assistants) == 1
        assert wf.assistants[0].skill_ids == []


class TestWorkflowConfigPoolFields:
    def test_parse_execution_config_pool_config(self):
        import yaml

        yaml_config = yaml.dump(
            {
                "pool_config": {
                    "enabled": True,
                    "min_size": 3,
                    "max_size": 10,
                    "refill_interval_seconds": 60,
                },
                "states": [],
            }
        )
        wf = WorkflowConfig(name="Test", description="Test", yaml_config=yaml_config)
        wf.parse_execution_config()
        assert wf.pool_config is not None
        assert wf.pool_config.enabled is True
        assert wf.pool_config.min_size == 3
        assert wf.pool_config.max_size == 10
        assert wf.pool_config.refill_interval_seconds == 60

    def test_parse_execution_config_no_pool_config_defaults_to_none(self):
        import yaml

        yaml_config = yaml.dump({"states": []})
        wf = WorkflowConfig(name="Test", description="Test", yaml_config=yaml_config)
        wf.parse_execution_config()
        assert wf.pool_config is None

    def test_parse_execution_config_max_nesting_level(self):
        import yaml

        yaml_config = yaml.dump({"max_nesting_level": 3, "states": []})
        wf = WorkflowConfig(name="Test", description="Test", yaml_config=yaml_config)
        wf.parse_execution_config()
        assert wf.max_nesting_level == 3

    def test_parse_execution_config_no_max_nesting_level_defaults_to_none(self):
        import yaml

        yaml_config = yaml.dump({"states": []})
        wf = WorkflowConfig(name="Test", description="Test", yaml_config=yaml_config)
        wf.parse_execution_config()
        assert wf.max_nesting_level is None

    def test_pool_config_field_defaults_to_none(self):
        wf = WorkflowConfig(name="Test", description="Test")
        assert wf.pool_config is None

    def test_max_nesting_level_defaults_to_none(self):
        wf = WorkflowConfig(name="Test", description="Test")
        assert wf.max_nesting_level is None


class TestWorkflowConfigUpdatePreservesHistory:
    def test_update_restores_db_history_when_not_passed(self):
        current = WorkflowConfig(
            id="wf_1",
            name="Old",
            description="d",
            yaml_config_history=[YamlConfigHistory(yaml_config="prior-yaml", date=datetime(2026, 1, 1))],
        )
        workflow = WorkflowConfig(id="wf_1", name="New", description="d", yaml_config_history=[])

        with (
            patch.object(WorkflowConfig, "find_by_id", return_value=current),
            patch.object(BaseModelWithSQLSupport, "update") as mock_super_update,
        ):
            workflow.update()

        assert [entry.yaml_config for entry in workflow.yaml_config_history] == ["prior-yaml"]
        mock_super_update.assert_called_once_with(refresh=False, validate=True)

    def test_update_preserves_null_history_when_not_passed(self):
        current = WorkflowConfig(id="wf_1", name="Old", description="d")
        current.yaml_config_history = None
        workflow = WorkflowConfig(id="wf_1", name="New", description="d", yaml_config_history=[])

        with (
            patch.object(WorkflowConfig, "find_by_id", return_value=current),
            patch.object(BaseModelWithSQLSupport, "update"),
        ):
            workflow.update()

        assert workflow.yaml_config_history is None

    def test_update_uses_explicit_history(self):
        current = WorkflowConfig(
            id="wf_1",
            name="Old",
            description="d",
            yaml_config_history=[YamlConfigHistory(yaml_config="prior-yaml", date=datetime(2026, 1, 1))],
        )
        workflow = WorkflowConfig(id="wf_1", name="New", description="d", yaml_config_history=[])
        explicit = [YamlConfigHistory(yaml_config="old: yaml", date=datetime(2026, 2, 1))]

        with (
            patch.object(WorkflowConfig, "find_by_id", return_value=current),
            patch.object(BaseModelWithSQLSupport, "update") as mock_super_update,
        ):
            workflow.update(yaml_config_history=explicit)

        assert workflow.yaml_config_history is explicit
        mock_super_update.assert_called_once_with(refresh=False, validate=True)

    def test_update_raises_when_workflow_is_missing(self):
        workflow = WorkflowConfig(id="missing", name="n", description="d")

        with (
            patch.object(WorkflowConfig, "find_by_id", return_value=None),
            pytest.raises(NotFoundException, match="WorkflowConfig missing not found"),
        ):
            workflow.update()


def test_template_from_yaml_extracts_required_variables():
    raw = (
        "name: Example\n"
        "description: Team ${input:team_name}\n"
        "slug: template-placeholder-test\n"
        "mode: Sequential\n"
        "start_hint: ${input:start_hint}\n"
        "execution_config:\n"
        "  assistants: []\n"
        "  states:\n"
        "    - id: step\n"
        '      assistant_id: "${input:assistant_id}"\n'
        "      task: do work\n"
        "      next: {}\n"
    )
    from codemie.core.workflow_models.workflow_config import WorkflowConfigTemplate

    tmpl = WorkflowConfigTemplate.from_yaml(raw)
    assert tmpl is not None
    assert tmpl.slug == "template-placeholder-test"
    assert "${input:team_name}" in tmpl.description
    assert "${input:assistant_id}" in tmpl.yaml_config
    assert tmpl.template_source == raw
    assert tmpl.required_variables == ["team_name", "start_hint", "assistant_id"]


def test_template_from_yaml_loads_mid_string_quoted_placeholder():
    raw = '''name: Example
description: "Process ${input:topic} data"
slug: template-placeholder-mid-string
mode: Sequential
start_hint: hint
execution_config:
  assistants: []
  states: []
'''
    from codemie.core.workflow_models.workflow_config import WorkflowConfigTemplate

    tmpl = WorkflowConfigTemplate.from_yaml(raw)
    assert tmpl is not None
    assert tmpl.description == "Process ${input:topic} data"
    assert tmpl.required_variables == ["topic"]


def test_template_from_yaml_keeps_template_with_numeric_field_placeholder():
    raw = (
        "name: Example\n"
        "description: desc\n"
        "slug: cr001-test\n"
        "mode: Sequential\n"
        "execution_config:\n"
        "  assistants: []\n"
        "  states: []\n"
        "  messages_limit_before_summarization: ${input:limit}\n"
    )
    from codemie.core.workflow_models.workflow_config import WorkflowConfigTemplate

    tmpl = WorkflowConfigTemplate.from_yaml(raw)
    assert tmpl is not None
    assert tmpl.required_variables == ["limit"]
    assert tmpl.template_source == raw


def test_template_required_variables_include_name_placeholders():
    raw = (
        "name: Workflow for ${input:wf_name}\n"
        "description: desc\n"
        "slug: cr004-test\n"
        "mode: Sequential\n"
        "execution_config:\n"
        "  assistants: []\n"
        "  states: []\n"
    )
    from codemie.core.workflow_models.workflow_config import WorkflowConfigTemplate

    tmpl = WorkflowConfigTemplate.from_yaml(raw)
    assert tmpl is not None
    assert tmpl.required_variables == ["wf_name"]


def test_template_required_variables_include_wrapper_placeholders():
    raw = (
        "name: Example\n"
        "description: desc\n"
        "slug: cr004-supervisor-test\n"
        "mode: Sequential\n"
        "supervisor_prompt: Handle ${input:supervisor_var} cases\n"
        "execution_config:\n"
        "  assistants: []\n"
        "  states: []\n"
    )
    from codemie.core.workflow_models.workflow_config import WorkflowConfigTemplate

    tmpl = WorkflowConfigTemplate.from_yaml(raw)
    assert tmpl is not None
    assert tmpl.required_variables == ["supervisor_var"]


def test_template_from_yaml_returns_none_when_execution_config_is_not_a_mapping():
    raw = (
        "name: Example\n"
        "description: desc\n"
        "slug: non-mapping-execution-config\n"
        "mode: Sequential\n"
        "execution_config: ${input:execution_config}\n"
    )
    from codemie.core.workflow_models.workflow_config import WorkflowConfigTemplate

    assert WorkflowConfigTemplate.from_yaml(raw) is None
