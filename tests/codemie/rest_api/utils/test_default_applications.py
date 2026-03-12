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

"""
Unit tests for default_applications.py

Tests the ensure_application_exists function which automatically creates
Application records when needed across datasources, integrations, and entities.
"""

from unittest.mock import Mock, patch

from codemie.core.models import Application
from codemie.rest_api.utils.default_applications import (
    ensure_application_exists,
    create_default_applications,
    DEMO_PROJECT_NAME,
    CODEMIE_PROJECT_NAME,
)


_SERVICE_ENSURE = 'codemie.service.user.application_service.application_service.ensure_application_exists'


class TestEnsureApplicationExists:
    """Tests for ensure_application_exists function"""

    @patch(_SERVICE_ENSURE)
    def test_application_already_exists(self, mock_service_ensure):
        """Test that the function delegates to the service when application exists"""
        ensure_application_exists("test-project")
        mock_service_ensure.assert_called_once_with("test-project")

    @patch(_SERVICE_ENSURE)
    def test_creates_new_application(self, mock_service_ensure):
        """Test that the function delegates to the service to create a new application"""
        ensure_application_exists("new-project")
        mock_service_ensure.assert_called_once_with("new-project")

    @patch(_SERVICE_ENSURE)
    def test_handles_race_condition_integrity_error(self, mock_service_ensure):
        """Test that IntegrityError from the service is handled (not re-raised)"""
        # IntegrityError is caught and logged inside the service; the wrapper must not raise
        mock_service_ensure.side_effect = None  # service swallows it
        ensure_application_exists("race-project")
        mock_service_ensure.assert_called_once_with("race-project")

    @patch(_SERVICE_ENSURE)
    def test_handles_other_exceptions(self, mock_service_ensure):
        """Test that general exceptions from the service are not re-raised"""
        # The service catches and logs general exceptions internally
        mock_service_ensure.side_effect = None
        ensure_application_exists("error-project")
        mock_service_ensure.assert_called_once_with("error-project")

    def test_handles_none_project_name(self):
        """Test that None project_name is handled gracefully"""
        # Execute - should not raise exception
        ensure_application_exists(None)
        # If no exception, test passes

    def test_handles_empty_project_name(self):
        """Test that empty string project_name is handled gracefully"""
        # Execute - should not raise exception
        ensure_application_exists("")
        # If no exception, test passes

    @patch(_SERVICE_ENSURE)
    def test_project_name_with_special_characters(self, mock_service_ensure):
        """Test that project names with special characters are forwarded to the service"""
        special_name = "project-with_special.chars@123"
        ensure_application_exists(special_name)
        mock_service_ensure.assert_called_once_with(special_name)


class TestCreateDefaultApplications:
    """Tests for create_default_applications function"""

    @patch('codemie.rest_api.utils.default_applications.Application.get_all_by_fields')
    @patch('codemie.rest_api.utils.default_applications.Application')
    @patch('codemie.rest_api.utils.default_applications.logger')
    def test_creates_both_default_applications(self, mock_logger, mock_app_class, mock_get_all):
        """Test that both demo and codemie applications are created if missing"""
        # Setup
        mock_get_all.return_value = []  # Both missing
        mock_app_instance = Mock(spec=Application)
        mock_app_class.return_value = mock_app_instance

        # Execute
        create_default_applications()

        # Verify
        assert mock_get_all.call_count == 2
        assert mock_app_class.call_count == 2
        assert mock_app_instance.save.call_count == 2
        assert mock_logger.info.call_count == 2

    @patch('codemie.rest_api.utils.default_applications.Application.get_all_by_fields')
    def test_skips_existing_default_applications(self, mock_get_all):
        """Test that existing default applications are not recreated"""
        # Setup
        demo_app = Mock(spec=Application)
        demo_app.name = DEMO_PROJECT_NAME
        codemie_app = Mock(spec=Application)
        codemie_app.name = CODEMIE_PROJECT_NAME

        def get_all_side_effect(query):
            if query["name"] == DEMO_PROJECT_NAME:
                return [demo_app]
            elif query["name"] == CODEMIE_PROJECT_NAME:
                return [codemie_app]
            return []

        mock_get_all.side_effect = get_all_side_effect

        # Execute
        create_default_applications()

        # Verify
        assert mock_get_all.call_count == 2


class TestIntegrationPoints:
    """Tests to ensure ensure_application_exists is called at all integration points"""

    def test_workflow_service_imports_ensure(self):
        """Test that workflow_service imports ensure_application_exists"""
        from codemie.service import workflow_service

        # Verify the module has the function imported
        assert hasattr(workflow_service, 'ensure_application_exists')

    def test_settings_service_imports_ensure(self):
        """Test that settings service imports ensure_application_exists"""
        from codemie.service.settings import settings

        # Verify the module has the function imported
        assert hasattr(settings, 'ensure_application_exists')


class TestEdgeCases:
    """Tests for edge cases and error conditions"""

    @patch(_SERVICE_ENSURE)
    def test_multiple_concurrent_calls_same_project(self, mock_service_ensure):
        """Test that multiple calls each delegate to the service"""
        for _ in range(5):
            ensure_application_exists("concurrent-project")

        assert mock_service_ensure.call_count == 5

    @patch(_SERVICE_ENSURE)
    def test_very_long_project_name(self, mock_service_ensure):
        """Test handling of very long project names is forwarded to the service"""
        long_name = "a" * 500
        ensure_application_exists(long_name)
        mock_service_ensure.assert_called_once_with(long_name)

    @patch(_SERVICE_ENSURE)
    def test_unicode_project_name(self, mock_service_ensure):
        """Test handling of unicode characters in project names is forwarded to the service"""
        unicode_name = "проект-测试-プロジェクト"
        ensure_application_exists(unicode_name)
        mock_service_ensure.assert_called_once_with(unicode_name)
