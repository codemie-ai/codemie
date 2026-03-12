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
Tests for system prompt validation API endpoint.

These tests verify that the /v1/assistants/system-prompt/validate endpoint
properly validates and renders system prompt templates with security protections.
"""

import pytest
from unittest.mock import patch
from fastapi import status
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.main import app
from codemie.rest_api.security.user import User


@pytest.fixture
def user():
    """Create a test user fixture."""
    return User(id="test-user-123", username="testuser", name="Test User")


@pytest.fixture(autouse=True)
def override_dependency(user):
    """Override authentication dependency for all tests."""
    from codemie.rest_api.routers import assistant as assistant_router

    app.dependency_overrides[assistant_router.authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


class TestSystemPromptValidationSuccess:
    """Test successful system prompt validation scenarios."""

    @pytest.mark.asyncio
    async def test_validate_simple_template(self):
        """Test validation of a simple template with valid variables."""
        request_data = {
            "system_prompt_template": "You are assisting {{ current_user }} on {{ project_name }}",
            "prompt_vars": {"current_user": "John Doe", "project_name": "AI_Project"},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        assert response_data["message"] == "System prompt rendered successfully"
        assert "John Doe" in response_data["rendered_prompt"]
        assert "AI_Project" in response_data["rendered_prompt"]

    @pytest.mark.asyncio
    async def test_validate_template_with_default_variables(self):
        """Test that default variables (current_user, date) are automatically added."""
        request_data = {
            "system_prompt_template": "You are helping {{ current_user }} on {{ date }}",
            "prompt_vars": {},  # Empty prompt_vars, should still work with defaults
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        # Date should be present in the rendered prompt
        assert "rendered_prompt" in response_data

    @pytest.mark.asyncio
    async def test_validate_template_with_assistant_id(self):
        """Test validation with assistant_id provided."""
        request_data = {
            "system_prompt_template": "Assistant {{ assistant_name }} for {{ project_name }}",
            "prompt_vars": {"assistant_name": "CodeBot", "project_name": "MyProject"},
            "assistant_id": "assistant-123",
        }

        # Mock the assistant_prompt_variable_mapping_service at the correct path
        with patch(
            "codemie.service.assistant.assistant_prompt_variable_mapping_service."
            "assistant_prompt_variable_mapping_service.get_user_variable_values",
            return_value={},
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                response = await ac.post(
                    "/v1/assistants/system-prompt/validate",
                    json=request_data,
                    headers={"Authorization": "Bearer testtoken"},
                )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        assert "CodeBot" in response_data["rendered_prompt"]
        assert "MyProject" in response_data["rendered_prompt"]

    @pytest.mark.asyncio
    async def test_validate_template_with_complex_jinja2_syntax(self):
        """Test validation with complex Jinja2 syntax (loops, conditionals)."""
        request_data = {
            "system_prompt_template": "{% if active %}Active: {{ name }}{% else %}Inactive{% endif %}",
            "prompt_vars": {"active": "true", "name": "TestBot"},  # active must be string for dict[str, str]
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        # Note: "true" string is truthy in Jinja2
        assert "Active: TestBot" in response_data["rendered_prompt"]

    @pytest.mark.asyncio
    async def test_validate_template_with_alphanumeric_underscore_keys(self):
        """Test validation with various valid key formats (alphanumeric + underscore)."""
        request_data = {
            "system_prompt_template": "{{ user_123 }} {{ project_name }} {{ var_ABC_def }}",
            "prompt_vars": {"user_123": "User123", "project_name": "Project", "var_ABC_def": "Value"},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        assert "User123" in response_data["rendered_prompt"]
        assert "Project" in response_data["rendered_prompt"]
        assert "Value" in response_data["rendered_prompt"]


class TestSystemPromptValidationSecurityViolations:
    """Test security violation scenarios (SSTI attacks, invalid variable names)."""

    @pytest.mark.asyncio
    async def test_reject_template_with_spaces_in_variable_name(self):
        """Test that templates with spaces in variable names are rejected."""
        request_data = {
            "system_prompt_template": "{{ my personal assistant }}",
            "prompt_vars": {},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response_data = response.json()
        assert "error" in response_data
        assert "System prompt validation failed" in response_data["error"]["message"]
        assert "Security violation" in response_data["error"]["details"]
        assert "invalid variable" in response_data["error"]["details"].lower()

    @pytest.mark.asyncio
    async def test_reject_template_with_hyphen_in_variable_name(self):
        """Test that templates with hyphens in variable names are rejected."""
        request_data = {
            "system_prompt_template": "{{ project-name }}",
            "prompt_vars": {},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response_data = response.json()
        assert "Security violation" in response_data["error"]["details"]

    @pytest.mark.asyncio
    async def test_reject_template_with_special_chars_in_variable_name(self):
        """Test that templates with special characters in variable names are rejected."""
        request_data = {
            "system_prompt_template": "{{ user@email }}",
            "prompt_vars": {},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response_data = response.json()
        assert "Security violation" in response_data["error"]["details"]

    @pytest.mark.asyncio
    async def test_reject_ssti_class_access_attempt(self):
        """Test that SSTI attack attempts using __class__ are blocked."""
        request_data = {
            "system_prompt_template": "{{ ''.__class__.__mro__[1].__subclasses__() }}",
            "prompt_vars": {},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response_data = response.json()
        assert "Security violation" in response_data["error"]["details"]

    @pytest.mark.asyncio
    async def test_reject_ssti_import_attempt(self):
        """Test that SSTI attack attempts using __import__ are blocked."""
        request_data = {
            "system_prompt_template": "{{ __import__('os').popen('ls').read() }}",
            "prompt_vars": {},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response_data = response.json()
        assert "Security violation" in response_data["error"]["details"]

    @pytest.mark.asyncio
    async def test_reject_ssti_globals_access_attempt(self):
        """Test that SSTI attack attempts using __globals__ are blocked."""
        request_data = {
            "system_prompt_template": "{{ func.__globals__ }}",
            "prompt_vars": {},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response_data = response.json()
        assert "Security violation" in response_data["error"]["details"]

    @pytest.mark.asyncio
    async def test_reject_ssti_eval_attempt(self):
        """Test that SSTI attack attempts using eval() are blocked."""
        request_data = {
            "system_prompt_template": "{{ eval('__import__(\"os\").system(\"ls\")') }}",
            "prompt_vars": {},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response_data = response.json()
        assert "Security violation" in response_data["error"]["details"]


class TestSystemPromptValidationEdgeCases:
    """Test edge cases and error scenarios."""

    @pytest.mark.asyncio
    async def test_validate_template_with_invalid_jinja2_syntax(self):
        """Test validation with invalid Jinja2 syntax - treated as plain text."""
        request_data = {
            "system_prompt_template": "{{ unclosed",
            "prompt_vars": {},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        # Invalid Jinja2 syntax is treated as plain text, not an error
        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        assert response_data["rendered_prompt"] == "{{ unclosed"  # Returned as-is

    @pytest.mark.asyncio
    async def test_validate_empty_template(self):
        """Test validation with empty template."""
        request_data = {
            "system_prompt_template": "",
            "prompt_vars": {},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        assert response_data["rendered_prompt"] == ""

    @pytest.mark.asyncio
    async def test_validate_template_without_variables(self):
        """Test validation of template without any variables."""
        request_data = {
            "system_prompt_template": "This is a static prompt with no variables.",
            "prompt_vars": {},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        assert response_data["rendered_prompt"] == "This is a static prompt with no variables."

    @pytest.mark.asyncio
    async def test_reject_invalid_key_in_prompt_vars(self):
        """Test that invalid keys in prompt_vars are filtered out."""
        request_data = {
            "system_prompt_template": "{{ valid_key }} {{ invalid_key }}",
            "prompt_vars": {
                "valid_key": "Valid",
                "invalid key": "Should be filtered",  # Space in key name
            },
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        assert "Valid" in response_data["rendered_prompt"]
        # invalid_key should not be rendered (filtered out by sanitization)

    @pytest.mark.asyncio
    async def test_validate_template_with_html_escaping(self):
        """Test that HTML in prompt variables is properly escaped."""
        request_data = {
            "system_prompt_template": "Content: {{ html_content }}",
            "prompt_vars": {"html_content": "<script>alert('XSS')</script>"},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        # HTML should be escaped
        assert "&lt;script&gt;" in response_data["rendered_prompt"]
        assert "<script>" not in response_data["rendered_prompt"]

    @pytest.mark.asyncio
    async def test_validate_missing_required_fields(self):
        """Test validation with missing required fields."""
        request_data = {
            "prompt_vars": {},
            # Missing system_prompt_template
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestSystemPromptValidationAuthentication:
    """Test authentication requirements for the endpoint."""

    @pytest.mark.asyncio
    async def test_reject_unauthenticated_request(self):
        """Test that unauthenticated requests are rejected."""
        request_data = {
            "system_prompt_template": "{{ name }}",
            "prompt_vars": {"name": "Test"},
        }

        # Remove authentication override for this test
        app.dependency_overrides = {}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                # No Authorization header
            )

        # Should return 401 or 403 depending on authentication setup
        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]


class TestSystemPromptValidationRealWorld:
    """Test real-world usage scenarios."""

    @pytest.mark.asyncio
    async def test_validate_typical_assistant_prompt(self):
        """Test validation of a typical assistant system prompt."""
        request_data = {
            "system_prompt_template": """You are {{ assistant_name }}, a helpful AI assistant.
You are helping {{ current_user }} with their {{ project_type }} project.
Today's date is {{ date }}.

Your capabilities include:
- Answering questions
- Providing code examples
- Explaining concepts

Please be helpful and concise in your responses.""",
            "prompt_vars": {
                "assistant_name": "CodeBot",
                "current_user": "Alice",
                "project_type": "Python",
            },
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        assert "CodeBot" in response_data["rendered_prompt"]
        assert "Alice" in response_data["rendered_prompt"]
        assert "Python" in response_data["rendered_prompt"]
        assert "capabilities" in response_data["rendered_prompt"]

    @pytest.mark.asyncio
    async def test_validate_prompt_with_special_value_content(self):
        """Test that variable values can contain special characters and spaces."""
        request_data = {
            "system_prompt_template": "Project: {{ project_desc }}",
            "prompt_vars": {
                "project_desc": "A web application with user authentication & data visualization (v2.0)",
            },
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/assistants/system-prompt/validate",
                json=request_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert response_data["is_valid"] is True
        # Values can contain spaces and special characters
        # Note: & is HTML-escaped to &amp; by Jinja2's autoescape
        assert "user authentication &amp; data visualization" in response_data["rendered_prompt"]
        assert "(v2.0)" in response_data["rendered_prompt"]
