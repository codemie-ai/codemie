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

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from codemie.service.settings.settings import SettingsService
from codemie.service.workflow_service import WorkflowService
from codemie.triggers.bindings.webhook import ResourceType, WebhookService


class TestGitHubWebhookIntegration:
    """Integration tests for complete GitHub webhook processing flow."""

    @pytest.fixture
    def github_webhook_secret(self):
        """GitHub webhook secret for testing."""
        return "test-github-webhook-secret-12345"

    @pytest.fixture
    def webhook_payload(self):
        """Sample GitHub PR webhook payload."""
        return b'{"action": "opened", "number": 42, "pull_request": {"id": 1}}'

    @pytest.fixture
    def create_github_webhook_request(self, webhook_payload, github_webhook_secret):
        """Factory to create GitHub webhook requests with valid signatures."""

        def _create_request(secret=None, tamper_signature=False, tamper_payload=False):
            request = MagicMock(spec=Request)

            actual_secret = secret or github_webhook_secret
            actual_payload = b'{"action": "closed"}' if tamper_payload else webhook_payload

            # Calculate signature
            signature = hmac.new(
                actual_secret.encode('utf-8'),
                webhook_payload,  # Always use original for signature
                hashlib.sha256,
            ).hexdigest()

            if tamper_signature:
                signature = "tampered_signature_12345"

            request.headers = {
                "X-Hub-Signature-256": f"sha256={signature}",
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "12345-67890-abcdef",
                "User-Agent": "GitHub-Hookshot/abc123",
                "Content-Type": "application/json",
            }
            request.body = AsyncMock(return_value=actual_payload)

            return request

        return _create_request

    @pytest.fixture
    def github_webhook_setting(self, github_webhook_secret):
        """Webhook settings configured for GitHub signature verification."""
        setting = MagicMock()
        webhook_id = "github-pr-webhook-123"

        setting.credential.side_effect = lambda key: {
            "webhook_id": webhook_id,
            WebhookService.GITHUB_WEBHOOK_SECRET: github_webhook_secret,
            WebhookService.GITHUB_EVENT_FILTER: "pull_request,push",
            WebhookService.GITHUB_REQUIRE_SHA256: True,
            WebhookService.RESOURCE_TYPE: ResourceType.WORKFLOW.value,
            WebhookService.RESOURCE_ID: "pr-review-workflow-456",
            WebhookService.IS_ENABLED: True,
            WebhookService.SECURE_HEADER_NAME: None,
            WebhookService.SECURE_HEADER_VALUE: None,
        }.get(key)

        setting.project_name = "test-project"
        setting.user_id = "user-123"
        setting.alias = "GitHub PR Webhook"

        return setting

    @pytest.mark.asyncio
    async def test_github_webhook_success_flow(self, create_github_webhook_request, github_webhook_setting):
        """Test successful GitHub webhook processing with signature verification."""
        webhook_id = "github-pr-webhook-123"
        request = create_github_webhook_request()
        background_tasks = MagicMock()

        workflow = MagicMock()
        workflow.created_by.user_id = "user-123"
        workflow.created_by.name = "Test User"
        workflow.created_by.username = "testuser"

        with patch.object(SettingsService, 'retrieve_setting', return_value=github_webhook_setting):
            with patch.object(WorkflowService, 'get_workflow', return_value=workflow):
                with patch('codemie.rest_api.routers.utils.run_in_thread_pool'):
                    response = await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)

                    assert response.message == WebhookService.WEBHOOK_INVOKED_SUCCESSFULLY
                    background_tasks.add_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_github_webhook_invalid_signature_rejected(
        self, create_github_webhook_request, github_webhook_setting
    ):
        """Test that webhooks with invalid signatures are rejected."""
        webhook_id = "github-pr-webhook-123"
        request = create_github_webhook_request(tamper_signature=True)
        background_tasks = MagicMock()

        with patch.object(SettingsService, 'retrieve_setting', return_value=github_webhook_setting):
            with pytest.raises(HTTPException) as exc_info:
                await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)

            assert exc_info.value.status_code == 401
            assert "Invalid" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_github_webhook_tampered_payload_rejected(
        self, create_github_webhook_request, github_webhook_setting
    ):
        """Test that webhooks with tampered payloads are rejected."""
        webhook_id = "github-pr-webhook-123"
        request = create_github_webhook_request(tamper_payload=True)
        background_tasks = MagicMock()

        with patch.object(SettingsService, 'retrieve_setting', return_value=github_webhook_setting):
            with pytest.raises(HTTPException) as exc_info:
                await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)

            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_github_webhook_event_filtering(self, create_github_webhook_request, github_webhook_setting):
        """Test that only allowed event types are processed."""
        webhook_id = "github-pr-webhook-123"
        request = create_github_webhook_request()
        background_tasks = MagicMock()

        # Change event to something not in filter
        request.headers["X-GitHub-Event"] = "release"  # Not in allowed: pull_request,push

        with patch.object(SettingsService, 'retrieve_setting', return_value=github_webhook_setting):
            with pytest.raises(HTTPException) as exc_info:
                await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)

            assert exc_info.value.status_code == 400
            assert "not allowed" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_github_webhook_missing_secret_config(self, create_github_webhook_request):
        """Test that webhook without secret falls back to no-security mode (logs warning)."""
        webhook_id = "github-pr-webhook-123"
        request = create_github_webhook_request()
        background_tasks = MagicMock()

        # Setting without GitHub secret (but has GitHub headers)
        setting = MagicMock()
        setting.credential.side_effect = lambda key: {
            "webhook_id": webhook_id,
            WebhookService.GITHUB_WEBHOOK_SECRET: None,  # No secret configured!
            WebhookService.RESOURCE_TYPE: ResourceType.WORKFLOW.value,
            WebhookService.RESOURCE_ID: "workflow-123",
            WebhookService.IS_ENABLED: True,
            WebhookService.SECURE_HEADER_NAME: None,
            WebhookService.SECURE_HEADER_VALUE: None,
        }.get(key)
        setting.project_name = "test-project"
        setting.user_id = "user-123"
        setting.alias = "Test Webhook"

        # Mock WorkflowService to avoid DB calls
        workflow = MagicMock()
        workflow.created_by.user_id = "user-123"
        workflow.created_by.name = "Test User"
        workflow.created_by.username = "testuser"

        with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
            with patch.object(WorkflowService, 'get_workflow', return_value=workflow):
                with patch('codemie.rest_api.routers.utils.run_in_thread_pool'):
                    # Should succeed (falls back to no-security mode with warning)
                    response = await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)

                    # Webhook is allowed (backward compatibility) but logs warning
                    assert response.message == WebhookService.WEBHOOK_INVOKED_SUCCESSFULLY

    @pytest.mark.asyncio
    async def test_github_webhook_with_secret_requires_valid_signature(
        self, create_github_webhook_request, github_webhook_secret
    ):
        """Test that when GitHub secret IS configured, invalid signatures are rejected."""
        webhook_id = "github-pr-webhook-123"
        request = create_github_webhook_request(tamper_signature=True)  # Invalid signature
        background_tasks = MagicMock()

        # Setting WITH GitHub secret
        setting = MagicMock()
        setting.credential.side_effect = lambda key: {
            "webhook_id": webhook_id,
            WebhookService.GITHUB_WEBHOOK_SECRET: github_webhook_secret,  # Secret IS configured
            WebhookService.RESOURCE_TYPE: ResourceType.WORKFLOW.value,
            WebhookService.RESOURCE_ID: "workflow-123",
            WebhookService.IS_ENABLED: True,
            WebhookService.SECURE_HEADER_NAME: None,
            WebhookService.SECURE_HEADER_VALUE: None,
        }.get(key)
        setting.project_name = "test-project"
        setting.user_id = "user-123"
        setting.alias = "Test Webhook"

        with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
            # Should raise 401 because signature is invalid
            with pytest.raises(HTTPException) as exc_info:
                await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)

            assert exc_info.value.status_code == 401
            assert "Invalid" in exc_info.value.detail


class TestBackwardCompatibility:
    """Test that legacy webhooks continue to work."""

    @pytest.mark.asyncio
    async def test_legacy_header_auth_still_works(self):
        """Test that existing webhooks with custom headers still work."""
        webhook_id = "legacy-webhook-123"

        request = MagicMock(spec=Request)
        request.headers = {"X-Custom-Token": "my-secret-token"}
        request.body = AsyncMock(return_value=b'{"data": "test"}')
        background_tasks = MagicMock()

        setting = MagicMock()
        setting.credential.side_effect = lambda key: {
            "webhook_id": webhook_id,
            WebhookService.SECURE_HEADER_NAME: "X-Custom-Token",
            WebhookService.SECURE_HEADER_VALUE: "my-secret-token",
            WebhookService.GITHUB_WEBHOOK_SECRET: None,
            WebhookService.RESOURCE_TYPE: ResourceType.ASSISTANT.value,
            WebhookService.RESOURCE_ID: "assistant-789",
            WebhookService.IS_ENABLED: True,
        }.get(key)
        setting.project_name = "legacy-project"
        setting.user_id = "user-456"
        setting.alias = "Legacy Webhook"

        assistant = MagicMock()
        assistant.created_by.id = "user-456"

        with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
            with patch('codemie.triggers.bindings.webhook.validate_assistant', return_value=assistant):
                with patch('codemie.rest_api.routers.utils.run_in_thread_pool'):
                    response = await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)

                    assert response.message == WebhookService.WEBHOOK_INVOKED_SUCCESSFULLY

    @pytest.mark.asyncio
    async def test_no_security_logs_warning(self):
        """Test that webhooks without security log warning but still work."""
        webhook_id = "insecure-webhook"

        request = MagicMock(spec=Request)
        request.headers = {}
        request.body = AsyncMock(return_value=b'{}')
        background_tasks = MagicMock()

        setting = MagicMock()
        setting.credential.side_effect = lambda key: {
            "webhook_id": webhook_id,
            WebhookService.GITHUB_WEBHOOK_SECRET: None,
            WebhookService.SECURE_HEADER_NAME: None,
            WebhookService.SECURE_HEADER_VALUE: None,
            WebhookService.RESOURCE_TYPE: ResourceType.ASSISTANT.value,
            WebhookService.RESOURCE_ID: "assistant-123",
            WebhookService.IS_ENABLED: True,
        }.get(key)
        setting.project_name = "test"
        setting.user_id = "user"
        setting.alias = "Insecure Webhook"

        assistant = MagicMock()
        assistant.created_by.id = "user"

        with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
            with patch('codemie.triggers.bindings.webhook.validate_assistant', return_value=assistant):
                with patch('codemie.rest_api.routers.utils.run_in_thread_pool'):
                    response = await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)

                    assert response.message == WebhookService.WEBHOOK_INVOKED_SUCCESSFULLY


class TestSecurityRequirements:
    """Test that security requirements are enforced correctly."""

    @pytest.mark.asyncio
    async def test_all_webhooks_checked(self, github_webhook_secret="test-secret"):
        """Verify that ALL webhook requests go through security verification."""
        webhook_id = "test-webhook"

        request = MagicMock(spec=Request)
        signature = hmac.new(github_webhook_secret.encode('utf-8'), b'{"test": "data"}', hashlib.sha256).hexdigest()
        request.headers = {
            "X-Hub-Signature-256": f"sha256={signature}",
            "X-GitHub-Event": "push",
        }
        request.body = AsyncMock(return_value=b'{"test": "data"}')
        background_tasks = MagicMock()

        setting = MagicMock()
        setting.credential.side_effect = lambda key: {
            "webhook_id": webhook_id,
            WebhookService.GITHUB_WEBHOOK_SECRET: github_webhook_secret,
            WebhookService.RESOURCE_TYPE: ResourceType.WORKFLOW.value,
            WebhookService.RESOURCE_ID: "workflow-id",
            WebhookService.IS_ENABLED: True,
        }.get(key)
        setting.project_name = "test"
        setting.user_id = "user"
        setting.alias = "Test"

        workflow = MagicMock()
        workflow.created_by.user_id = "user"
        workflow.created_by.name = "User"
        workflow.created_by.username = "user"

        with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
            with patch.object(WorkflowService, 'get_workflow', return_value=workflow):
                with patch('codemie.rest_api.routers.utils.run_in_thread_pool'):
                    # Should succeed - signature is valid
                    response = await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)
                    assert response.message == WebhookService.WEBHOOK_INVOKED_SUCCESSFULLY

    @pytest.mark.asyncio
    async def test_invalid_requests_rejected(self, github_webhook_secret="test-secret"):
        """Verify that invalid webhook requests are rejected."""
        webhook_id = "test-webhook"

        request = MagicMock(spec=Request)
        request.headers = {
            "X-Hub-Signature-256": "sha256=invalid_signature",
            "X-GitHub-Event": "push",
        }
        request.body = AsyncMock(return_value=b'{"test": "data"}')
        background_tasks = MagicMock()

        setting = MagicMock()
        setting.credential.side_effect = lambda key: {
            "webhook_id": webhook_id,
            WebhookService.GITHUB_WEBHOOK_SECRET: github_webhook_secret,
            WebhookService.RESOURCE_TYPE: ResourceType.WORKFLOW.value,
            WebhookService.RESOURCE_ID: "workflow-id",
            WebhookService.IS_ENABLED: True,
        }.get(key)
        setting.project_name = "test"
        setting.user_id = "user"
        setting.alias = "Test"

        with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
            with pytest.raises(HTTPException) as exc_info:
                await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)

            # Invalid request should be rejected with 401
            assert exc_info.value.status_code == 401
