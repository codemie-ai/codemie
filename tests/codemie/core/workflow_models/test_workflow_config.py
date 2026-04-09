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
from unittest.mock import MagicMock

from codemie.configs import config
from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowRetryPolicy,
    RETRY_POLICY_DEFAULT_BACKOFF_FACTOR,
    RETRY_POLICY_DEFAULT_MAX_INTERVAL,
    RETRY_POLICY_DEFAULT_MAX_ATTEMPTS,
    RETRY_POLICY_DEFAULT_INITIAL_INTERVAL,
)
from codemie.rest_api.security.user import User


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
        assert len(result.assistants) == 1
        assert result.assistants[0].id == "assistant_1"

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
