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
Unit tests for User model
"""

from unittest.mock import patch

from codemie.rest_api.security.user import User


class TestUserModel:
    """Test cases for User model properties"""

    def test_user_creation_with_defaults(self):
        """Test that user can be created with default values"""
        user = User(id="test_id", username="testuser")

        assert user.id == "test_id"
        assert user.username == "testuser"
        assert user.name == ""
        assert user.roles == []
        assert user.project_names == ['demo']
        assert user.admin_project_names == []
        assert user.picture == ""
        assert user.knowledge_bases == []
        # user_type defaults to 'regular' when not specified (see user.py line 20)
        assert user.user_type == 'regular'
        assert user.auth_token is None

    def test_user_creation_with_all_fields(self):
        """Test that user can be created with all fields specified"""
        user = User(
            id="test_id",
            username="testuser",
            name="Test User",
            roles=["admin", "user"],
            project_names=["app1", "app2"],
            admin_project_names=["app1"],
            picture="http://example.com/pic.jpg",
            knowledge_bases=["kb1", "kb2"],
            user_type="external",
            auth_token="token123",
        )

        assert user.id == "test_id"
        assert user.username == "testuser"
        assert user.name == "Test User"
        assert user.roles == ["admin", "user"]
        assert user.project_names == ["app1", "app2"]
        assert user.admin_project_names == ["app1"]
        assert user.picture == "http://example.com/pic.jpg"
        assert user.knowledge_bases == ["kb1", "kb2"]
        assert user.user_type == "external"
        assert user.auth_token == "token123"


class TestIsExternalUser:
    """Test cases for is_external_user property"""

    def test_is_external_user_when_user_type_external(self):
        """Test that is_external_user returns True for external user type"""
        user = User(id="test", username="test", user_type="external")
        assert user.is_external_user is True

    def test_is_external_user_when_user_type_internal(self):
        """Test that is_external_user checks if user_type equals 'external'"""
        user = User(id="test", username="test", user_type="internal")
        assert user.is_external_user is False

    def test_is_external_user_when_user_type_none(self):
        """Test that is_external_user returns False when user_type is None"""
        user = User(id="test", username="test", user_type=None)
        assert user.is_external_user is False

    def test_is_external_user_when_user_type_not_set(self):
        """Test that is_external_user returns False when user_type is not provided"""
        user = User(id="test", username="test")
        assert user.is_external_user is False


class TestUserProperties:
    """Test cases for various User properties"""

    def test_full_name_uses_username_when_available(self):
        """Test that full_name returns username when it's set"""
        user = User(id="123", username="testuser", name="Test Name")
        assert user.full_name == "testuser"

    def test_full_name_uses_name_when_username_empty(self):
        """Test that full_name returns name when username is empty"""
        user = User(id="123", username="", name="Test Name")
        assert user.full_name == "Test Name"

    def test_full_name_uses_id_when_both_empty(self):
        """Test that full_name returns id when both username and name are empty"""
        user = User(id="123", username="", name="")
        assert user.full_name == "123"

    def test_is_demo_user_when_demo_role_present(self):
        """Test that is_demo_user returns True when demo_user role is present"""
        user = User(id="test", username="test", roles=["demo_user", "other"])
        assert user.is_demo_user is True

    def test_is_demo_user_when_demo_role_absent(self):
        """Test that is_demo_user returns False when demo_user role is not present"""
        user = User(id="test", username="test", roles=["admin", "user"])
        assert user.is_demo_user is False

    def test_current_project_returns_first_application(self):
        """Test that current_project returns first application"""
        user = User(id="test", username="test", project_names=["app1", "app2"])
        assert user.current_project == "app1"

    def test_current_project_returns_demo_when_no_applications(self):
        """Test that current_project returns DEMO_PROJECT when applications is empty"""
        user = User(id="test", username="test", project_names=[])
        # Should return first from default applications
        assert user.current_project in ["demo", "codemie"]

    @patch('codemie.rest_api.security.user.config.ENV', 'production')
    @patch('codemie.rest_api.security.user.config.ADMIN_USER_ID', None)
    @patch('codemie.rest_api.security.user.config.ADMIN_ROLE_NAME', 'admin')
    def test_has_access_to_application_for_regular_user(self):
        """Test that has_access_to_application works for regular users"""
        user = User(id="test", username="test", project_names=["app1", "app2"])
        assert user.has_access_to_application("app1") is True
        assert user.has_access_to_application("app3") is False

    @patch('codemie.rest_api.security.user.config.ENV', 'production')
    @patch('codemie.rest_api.security.user.config.ADMIN_USER_ID', None)
    @patch('codemie.rest_api.security.user.config.ADMIN_ROLE_NAME', 'admin')
    def test_has_access_to_application_for_application_admin_only(self):
        """Test that application admin has access even if application is not in their applications list"""
        user = User(id="test", username="test", project_names=["app1"], admin_project_names=["app2", "app3"], roles=[])
        # User has regular access to app1
        assert user.has_access_to_application("app1") is True
        # User has admin access to app2 and app3, even though not in applications list
        assert user.has_access_to_application("app2") is True
        assert user.has_access_to_application("app3") is True
        # User has no access to app4
        assert user.has_access_to_application("app4") is False

    @patch('codemie.rest_api.security.user.config.ENV', 'production')
    @patch('codemie.rest_api.security.user.config.ADMIN_USER_ID', None)
    @patch('codemie.rest_api.security.user.config.ADMIN_ROLE_NAME', 'admin')
    def test_has_access_to_application_for_application_admin_without_regular_applications(self):
        """Test that application admin has access when they have no applications but only applications_admin"""
        user = User(id="test", username="test", project_names=[], admin_project_names=["app1"], roles=[])
        # User should have access to app1 via application admin
        assert user.has_access_to_application("app1") is True
        # User should not have access to app2
        assert user.has_access_to_application("app2") is False

    @patch('codemie.rest_api.security.user.config.ENV', 'production')
    @patch('codemie.rest_api.security.user.config.ADMIN_USER_ID', None)
    @patch('codemie.rest_api.security.user.config.ADMIN_ROLE_NAME', 'admin')
    def test_has_access_to_kb_for_regular_user(self):
        """Test that has_access_to_kb works for regular users"""
        user = User(id="test", username="test", knowledge_bases=["kb1", "kb2"])
        assert user.has_access_to_kb("kb1") is True
        assert user.has_access_to_kb("kb3") is False

    def test_is_application_admin(self):
        """Test that is_application_admin checks applications_admin list"""
        user = User(id="test", username="test", admin_project_names=["app1"])
        assert user.is_application_admin("app1") is True
        assert user.is_application_admin("app2") is False


class TestUserAsUserModel:
    """Test cases for as_user_model method"""

    def test_as_user_model_returns_user_entity(self):
        """Test that as_user_model returns UserEntity with correct fields"""
        user = User(id="123", username="testuser", name="Test Name")
        user_entity = user.as_user_model()

        assert user_entity.user_id == "123"
        assert user_entity.username == "testuser"
        assert user_entity.name == "Test Name"
