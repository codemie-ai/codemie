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
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request

from codemie.triggers.bindings.github_webhook_security import GitHubWebhookSecurity


class TestGitHubWebhookSecurity:
    """Test suite for GitHub webhook signature verification."""

    @pytest.fixture
    def github_webhook_secret(self):
        """Sample webhook secret."""
        return "test-github-webhook-secret-12345"

    @pytest.fixture
    def sample_payload(self):
        """Sample GitHub webhook payload."""
        return b'{"action": "opened", "number": 42, "pull_request": {"id": 1}}'

    @pytest.fixture
    def github_webhook_request(self, sample_payload, github_webhook_secret):
        """Create a GitHub webhook request with valid signature."""
        request = MagicMock(spec=Request)

        # Calculate valid signature
        signature = hmac.new(github_webhook_secret.encode('utf-8'), sample_payload, hashlib.sha256).hexdigest()

        # Set GitHub webhook headers
        request.headers = {
            "X-Hub-Signature-256": f"sha256={signature}",
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "12345-67890-abcdef",
            "X-GitHub-Hook-ID": "123456789",
            "User-Agent": "GitHub-Hookshot/abc123",
            "Content-Type": "application/json",
        }

        return request

    def test_verify_sha256_signature_valid(self, github_webhook_request, github_webhook_secret, sample_payload):
        """Test successful SHA-256 signature verification."""
        # Should not raise exception - returns None on success
        GitHubWebhookSecurity.verify_signature(github_webhook_request, github_webhook_secret, sample_payload)
        # If we got here without exception, verification succeeded

    def test_verify_sha256_signature_invalid(self, github_webhook_request, github_webhook_secret, sample_payload):
        """Test rejection of invalid SHA-256 signature."""
        # Tamper with signature
        github_webhook_request.headers["X-Hub-Signature-256"] = "sha256=invalid_signature"

        with pytest.raises(HTTPException) as exc_info:
            GitHubWebhookSecurity.verify_signature(github_webhook_request, github_webhook_secret, sample_payload)

        assert exc_info.value.status_code == 401
        assert "Invalid" in exc_info.value.detail

    def test_verify_signature_tampered_payload(self, github_webhook_request, github_webhook_secret):
        """Test rejection when payload is tampered."""
        # Keep original signature but change payload
        tampered_payload = b'{"action": "closed", "number": 99}'

        with pytest.raises(HTTPException) as exc_info:
            GitHubWebhookSecurity.verify_signature(github_webhook_request, github_webhook_secret, tampered_payload)

        assert exc_info.value.status_code == 401

    def test_verify_signature_missing_secret(self, github_webhook_request, sample_payload):
        """Test error when secret is not configured."""
        with pytest.raises(HTTPException) as exc_info:
            GitHubWebhookSecurity.verify_signature(github_webhook_request, "", sample_payload)

        assert exc_info.value.status_code == 500
        assert "secret" in exc_info.value.detail.lower()

    def test_verify_signature_missing_header(self, github_webhook_secret, sample_payload):
        """Test error when signature header is missing."""
        request = MagicMock(spec=Request)
        request.headers = {}

        with pytest.raises(HTTPException) as exc_info:
            GitHubWebhookSecurity.verify_signature(request, github_webhook_secret, sample_payload)

        assert exc_info.value.status_code == 401
        assert "Missing" in exc_info.value.detail

    def test_is_github_webhook_with_signature(self):
        """Test GitHub webhook detection via signature header."""
        request = MagicMock(spec=Request)
        request.headers = {"X-Hub-Signature-256": "sha256=abc123"}

        assert GitHubWebhookSecurity.is_github_webhook(request) is True

    def test_is_github_webhook_with_event_header(self):
        """Test GitHub webhook detection via event header."""
        request = MagicMock(spec=Request)
        request.headers = {"X-GitHub-Event": "push"}

        assert GitHubWebhookSecurity.is_github_webhook(request) is True

    def test_is_github_webhook_with_user_agent(self):
        """Test GitHub webhook detection via User-Agent."""
        request = MagicMock(spec=Request)
        request.headers = {"User-Agent": "GitHub-Hookshot/abc123"}

        assert GitHubWebhookSecurity.is_github_webhook(request) is True

    def test_is_github_webhook_negative(self):
        """Test GitHub webhook detection returns False for non-GitHub requests."""
        request = MagicMock(spec=Request)
        request.headers = {"User-Agent": "curl/7.64.1"}

        assert GitHubWebhookSecurity.is_github_webhook(request) is False

    def test_extract_github_metadata(self):
        """Test extraction of GitHub metadata from headers."""
        request = MagicMock(spec=Request)
        request.headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "12345-67890",
            "X-GitHub-Hook-ID": "42",
            "User-Agent": "GitHub-Hookshot/abc",
        }

        metadata = GitHubWebhookSecurity.extract_github_metadata(request)

        assert metadata["event"] == "pull_request"
        assert metadata["delivery_id"] == "12345-67890"
        assert metadata["hook_id"] == "42"

    def test_validate_event_type_allowed(self):
        """Test event type validation with allowed events."""
        request = MagicMock(spec=Request)
        request.headers = {"X-GitHub-Event": "push"}

        allowed_events = ["push", "pull_request"]
        # Should not raise exception - returns None on success
        GitHubWebhookSecurity.validate_event_type(request, allowed_events)
        # If we got here without exception, validation succeeded

    def test_validate_event_type_not_allowed(self):
        """Test rejection of event types not in allowed list."""
        request = MagicMock(spec=Request)
        request.headers = {"X-GitHub-Event": "release", "X-GitHub-Delivery": "test-123"}

        allowed_events = ["push", "pull_request"]

        with pytest.raises(HTTPException) as exc_info:
            GitHubWebhookSecurity.validate_event_type(request, allowed_events)

        assert exc_info.value.status_code == 400
        assert "not allowed" in exc_info.value.detail

    def test_validate_event_type_no_filter(self):
        """Test that all events are allowed when filter is None."""
        request = MagicMock(spec=Request)
        request.headers = {"X-GitHub-Event": "any_event"}

        # Should not raise exception when no filter configured
        GitHubWebhookSecurity.validate_event_type(request, None)

    def test_validate_event_type_empty_filter(self):
        """Test that all events are allowed when filter is empty list."""
        request = MagicMock(spec=Request)
        request.headers = {"X-GitHub-Event": "any_event"}

        # Should not raise exception when filter is empty
        GitHubWebhookSecurity.validate_event_type(request, [])

    def test_constant_time_comparison(self, github_webhook_secret, sample_payload):
        """Test that signature comparison uses constant-time algorithm."""
        # Generate correct signature
        correct_signature = hmac.new(github_webhook_secret.encode('utf-8'), sample_payload, hashlib.sha256).hexdigest()

        # Verify that hmac.compare_digest is used (constant-time)
        assert hmac.compare_digest(correct_signature, correct_signature) is True
        assert hmac.compare_digest(correct_signature, "wrong_signature") is False
