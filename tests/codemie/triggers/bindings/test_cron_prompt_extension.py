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
from unittest.mock import MagicMock, patch
from datetime import datetime

from codemie.triggers.bindings.cron import Cron
from codemie.rest_api.models.settings import Settings, CredentialValues


@pytest.fixture
def cron_instance():
    """Create a Cron instance for testing"""
    cron = Cron()
    cron.scheduler = MagicMock()
    cron.jobs = {}
    return cron


@pytest.fixture
def mock_setting_with_prompt():
    """Create a mock setting with a custom prompt"""
    setting = MagicMock(spec=Settings)
    setting.id = "test_setting_id"
    setting.user_id = "test_user"
    setting.update_date = datetime.now()
    setting.credential_values = [
        CredentialValues(key="schedule", value="0 9 * * 1-5"),
        CredentialValues(key="resource_type", value="assistant"),
        CredentialValues(key="resource_id", value="assistant_123"),
        CredentialValues(key="is_enabled", value=True),
        CredentialValues(key="prompt", value="Analyze the daily reports and summarize key findings"),
    ]
    return setting


@pytest.fixture
def mock_setting_without_prompt():
    """Create a mock setting without a custom prompt"""
    setting = MagicMock(spec=Settings)
    setting.id = "test_setting_id_no_prompt"
    setting.user_id = "test_user"
    setting.update_date = datetime.now()
    setting.credential_values = [
        CredentialValues(key="schedule", value="0 9 * * 1-5"),
        CredentialValues(key="resource_type", value="workflow"),
        CredentialValues(key="resource_id", value="workflow_123"),
        CredentialValues(key="is_enabled", value=True),
    ]
    return setting


@pytest.fixture
def mock_setting_with_long_prompt():
    """Create a mock setting with a very long prompt"""
    setting = MagicMock(spec=Settings)
    setting.id = "test_setting_long_prompt"
    setting.user_id = "test_user"
    setting.update_date = datetime.now()
    long_prompt = "A" * 5000  # 5000 characters, exceeds 4000 limit
    setting.credential_values = [
        CredentialValues(key="schedule", value="0 9 * * 1-5"),
        CredentialValues(key="resource_type", value="assistant"),
        CredentialValues(key="resource_id", value="assistant_123"),
        CredentialValues(key="is_enabled", value=True),
        CredentialValues(key="prompt", value=long_prompt),
    ]
    return setting


class TestCronPromptExtension:
    """Test cases for the prompt extension functionality"""

    @patch('codemie.triggers.bindings.cron.validate_assistant')
    def test_valid_setting_with_custom_prompt(self, mock_validate_assistant, cron_instance, mock_setting_with_prompt):
        """Test that __valid_setting extracts custom prompt correctly"""
        # Mock assistant validation
        mock_validate_assistant.return_value = MagicMock(name="Test Assistant")

        result = cron_instance._Cron__valid_setting(mock_setting_with_prompt)

        assert result is not False
        assert result["prompt"] == "Analyze the daily reports and summarize key findings"
        assert result["resource_type"] == "assistant"
        assert result["resource_id"] == "assistant_123"

    @patch('codemie.triggers.bindings.cron.validate_datasource')
    def test_valid_setting_without_prompt_defaults(
        self, mock_validate_datasource, cron_instance, mock_setting_without_prompt
    ):
        """Test that __valid_setting defaults to 'Do it' when no prompt provided"""
        # Change resource type to avoid assistant validation
        mock_setting_without_prompt.credential_values[1].value = "datasource"
        mock_setting_without_prompt.credential_values.append(CredentialValues(key="resource_type", value="datasource"))

        # Mock datasource validation - return a mock object with attributes, not a dictionary
        mock_datasource = MagicMock()
        mock_datasource.project_name = "test_app"
        mock_datasource.repo_name = "test_repo"
        mock_datasource.index_type = "code"
        mock_datasource.jira = None
        mock_validate_datasource.return_value = mock_datasource

        result = cron_instance._Cron__valid_setting(mock_setting_without_prompt)

        assert result is not False
        assert result["prompt"] == "Do it"

    @patch('codemie.triggers.bindings.cron.validate_assistant')
    @patch('codemie.triggers.bindings.cron.config')
    def test_valid_setting_with_long_prompt_truncation(
        self, mock_config, mock_validate_assistant, cron_instance, mock_setting_with_long_prompt
    ):
        """Test that long prompts are truncated to configured limit"""
        mock_validate_assistant.return_value = MagicMock(name="Test Assistant")
        mock_config.SCHEDULER_PROMPT_SIZE_LIMIT = 4000

        with patch('codemie.triggers.bindings.cron.logger') as mock_logger:
            result = cron_instance._Cron__valid_setting(mock_setting_with_long_prompt)

            assert result is not False
            assert len(result["prompt"]) == 4000
            assert result["prompt"] == "A" * 4000
            mock_logger.warning.assert_called_once()

    @patch('codemie.triggers.bindings.cron.validate_assistant')
    def test_valid_setting_with_empty_prompt_after_strip(self, mock_validate_assistant, cron_instance):
        """Test that empty prompt after stripping defaults to 'Do it'"""
        mock_validate_assistant.return_value = MagicMock(name="Test Assistant")

        setting = MagicMock(spec=Settings)
        setting.id = "test_setting_empty_prompt"
        setting.user_id = "test_user"
        setting.update_date = datetime.now()
        setting.credential_values = [
            CredentialValues(key="schedule", value="0 9 * * 1-5"),
            CredentialValues(key="resource_type", value="assistant"),
            CredentialValues(key="resource_id", value="assistant_123"),
            CredentialValues(key="is_enabled", value=True),
            CredentialValues(key="prompt", value="   \n\t   "),  # Whitespace only
        ]

        result = cron_instance._Cron__valid_setting(setting)

        assert result is not False
        assert result["prompt"] == "Do it"

    @patch('codemie.triggers.bindings.cron.invoke_assistant')
    @patch('codemie.triggers.bindings.cron.CronTrigger')
    def test_actualize_assistant_job_with_custom_prompt(self, mock_cron_trigger, mock_invoke_assistant, cron_instance):
        """Test that assistant jobs are created with custom prompts"""
        mock_trigger = MagicMock()
        mock_cron_trigger.return_value = mock_trigger
        mock_instance = MagicMock()
        cron_instance.scheduler.add_job.return_value = mock_instance

        custom_prompt = "Generate weekly status report"

        cron_instance._Cron__actualize_cron_job(
            cron_expression="0 9 * * 1-5",
            resource_id="assistant_123",
            resource_type="assistant",
            job_id="test_job",
            is_enabled=True,
            user_id="test_user",
            resource_name="Test Assistant",
            prompt=custom_prompt,
        )

        # Verify job was scheduled with correct parameters
        cron_instance.scheduler.add_job.assert_called_once_with(
            mock_invoke_assistant,
            trigger=mock_trigger,
            id="test_job",
            replace_existing=True,
            kwargs={
                "assistant_id": "assistant_123",
                "user_id": "test_user",
                "job_id": "test_job",
                "task": custom_prompt,
            },
        )

        # Verify job was added to jobs dict
        assert "test_job" in cron_instance.jobs

    @patch('codemie.triggers.bindings.cron.invoke_workflow')
    @patch('codemie.triggers.bindings.cron.CronTrigger')
    def test_actualize_workflow_job_with_custom_prompt(self, mock_cron_trigger, mock_invoke_workflow, cron_instance):
        """Test that workflow jobs are created with custom prompts"""
        mock_trigger = MagicMock()
        mock_cron_trigger.return_value = mock_trigger
        mock_instance = MagicMock()
        cron_instance.scheduler.add_job.return_value = mock_instance

        custom_prompt = "Process customer feedback data"

        cron_instance._Cron__actualize_cron_job(
            cron_expression="0 9 * * 1-5",
            resource_id="workflow_123",
            resource_type="workflow",
            job_id="test_workflow_job",
            is_enabled=True,
            user_id="test_user",
            prompt=custom_prompt,
        )

        # Verify job was scheduled with correct parameters
        cron_instance.scheduler.add_job.assert_called_once_with(
            mock_invoke_workflow,
            trigger=mock_trigger,
            id="test_workflow_job",
            replace_existing=True,
            kwargs={
                "workflow_id": "workflow_123",
                "user_id": "test_user",
                "job_id": "test_workflow_job",
                "task": custom_prompt,
            },
        )

    @patch('codemie.triggers.bindings.cron.invoke_assistant')
    @patch('codemie.triggers.bindings.cron.CronTrigger')
    def test_actualize_job_without_prompt_uses_default(self, mock_cron_trigger, mock_invoke_assistant, cron_instance):
        """Test that jobs without prompts use default 'Do it'"""
        mock_trigger = MagicMock()
        mock_cron_trigger.return_value = mock_trigger
        mock_instance = MagicMock()
        cron_instance.scheduler.add_job.return_value = mock_instance

        cron_instance._Cron__actualize_cron_job(
            cron_expression="0 9 * * 1-5",
            resource_id="assistant_123",
            resource_type="assistant",
            job_id="test_job",
            is_enabled=True,
            user_id="test_user",
            resource_name="Test Assistant",
            prompt=None,
        )

        # Verify job was scheduled with default task
        call_kwargs = cron_instance.scheduler.add_job.call_args[1]["kwargs"]
        assert call_kwargs["task"] == "Do it"

    @patch('codemie.triggers.bindings.cron.logger')
    def test_logging_for_custom_prompts(self, mock_logger, cron_instance):
        """Test that custom prompt usage is logged"""
        with patch('codemie.triggers.bindings.cron.CronTrigger'), patch.object(cron_instance.scheduler, 'add_job'):
            custom_prompt = "A very long custom prompt that should be truncated in the log message"

            cron_instance._Cron__actualize_cron_job(
                cron_expression="0 9 * * 1-5",
                resource_id="assistant_123",
                resource_type="assistant",
                job_id="test_job",
                is_enabled=True,
                user_id="test_user",
                resource_name="Test Assistant",
                prompt=custom_prompt,
            )

            # Verify logging was called with truncated prompt (now includes resource_name)
            mock_logger.info.assert_called_with(
                "Scheduling assistant job %s (%s) with custom prompt: %s...",
                "test_job",
                "Test Assistant",
                custom_prompt[:50],
            )

    def test_get_cred_value_helper(self, cron_instance):
        """Test the __get_cred_value helper method works correctly"""
        setting = MagicMock(spec=Settings)
        setting.credential_values = [
            CredentialValues(key="prompt", value="Test prompt"),
            CredentialValues(key="schedule", value="0 9 * * *"),
        ]

        # Test existing key
        result = cron_instance._Cron__get_cred_value(setting, "prompt")
        assert result == "Test prompt"

        # Test non-existing key
        result = cron_instance._Cron__get_cred_value(setting, "nonexistent")
        assert result is None
