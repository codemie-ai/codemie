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
from unittest.mock import Mock

from fastapi import HTTPException

from codemie.triggers.bindings.gitlab_webhook_security import GitLabWebhookSecurity


@pytest.fixture
def mock_gitlab_request():
    """Mock FastAPI Request with GitLab headers."""
    request = Mock()
    request.headers = {
        "X-Gitlab-Event": "Merge Request Hook",
        "X-Gitlab-Delivery": "12345-67890-abcdef",
        "X-Gitlab-Token": "test_token_signature",
        "User-Agent": "GitLab/16.0",
    }
    request.body = b'{"object_kind":"merge_request","action":"open"}'
    return request


def test_is_gitlab_webhook_with_gitlab_headers(mock_gitlab_request):
    """Test detection of GitLab webhook from headers."""
    assert GitLabWebhookSecurity.is_gitlab_webhook(mock_gitlab_request) is True


def test_is_gitlab_webhook_with_github_headers():
    """Test that GitHub webhooks are NOT detected as GitLab."""
    request = Mock()
    request.headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": "sha256=abcd1234",
        "User-Agent": "GitHub-Hookshot/123abc",
    }
    assert GitLabWebhookSecurity.is_gitlab_webhook(request) is False


def test_extract_mr_action_open(gitlab_mr_open_payload):
    """Test extraction of MR action 'open' from GitLab event."""
    action = GitLabWebhookSecurity.extract_mr_action(gitlab_mr_open_payload)
    assert action == "open"


def test_extract_mr_action_merge(gitlab_mr_merge_payload):
    """Test extraction of MR action 'merge' from GitLab event."""
    action = GitLabWebhookSecurity.extract_mr_action(gitlab_mr_merge_payload)
    assert action == "merge"


def test_extract_mr_action_non_mr_event():
    """Test extraction returns None for non-MR events."""
    payload = {"object_kind": "push", "action": None}
    action = GitLabWebhookSecurity.extract_mr_action(payload)
    assert action is None


def test_extract_mr_action_invalid_utf8_bytes_returns_none():
    """Invalid UTF-8 is rejected outright, not silently repaired before parsing."""
    corrupted = b'{"object_kind":"merge_request","action":"open"\xff\xfe}'
    assert GitLabWebhookSecurity.extract_mr_action(corrupted) is None


def test_validate_mr_event_type_allowed(gitlab_mr_open_payload):
    """Test MR action validation passes for allowed actions."""
    allowed_actions = ["open", "merge"]
    result = GitLabWebhookSecurity.validate_mr_event_type(gitlab_mr_open_payload, allowed_actions)
    assert result is True


def test_validate_mr_event_type_not_allowed(gitlab_mr_merge_payload):
    """Test MR action validation fails for disallowed actions."""
    allowed_actions = ["open"]  # merge not allowed
    result = GitLabWebhookSecurity.validate_mr_event_type(gitlab_mr_merge_payload, allowed_actions)
    assert result is False


def test_validate_mr_event_type_empty_filter(gitlab_mr_open_payload):
    """Test MR action validation passes when no filter is specified."""
    allowed_actions = []
    result = GitLabWebhookSecurity.validate_mr_event_type(gitlab_mr_open_payload, allowed_actions)
    assert result is True


def test_validate_mr_event_type_non_mr_event_passes_through():
    """Non-MR events (push, pipeline, tag, note, …) are outside the filter's scope
    and MUST pass through — a single webhook URL can receive multiple event types.
    """
    push_payload = {"object_kind": "push", "action": None}
    assert GitLabWebhookSecurity.validate_mr_event_type(push_payload, ["open"]) is True


def test_apply_mr_action_filter_no_filter_dispatches(gitlab_mr_open_payload):
    dispatch, action = GitLabWebhookSecurity.apply_mr_action_filter(gitlab_mr_open_payload, None)
    assert dispatch is True and action is None
    dispatch, action = GitLabWebhookSecurity.apply_mr_action_filter(gitlab_mr_open_payload, "")
    assert dispatch is True and action is None


def test_apply_mr_action_filter_mr_action_allowed(gitlab_mr_open_payload):
    dispatch, action = GitLabWebhookSecurity.apply_mr_action_filter(gitlab_mr_open_payload, "open,merge")
    assert dispatch is True and action is None


def test_apply_mr_action_filter_mr_action_blocked(gitlab_mr_merge_payload):
    dispatch, action = GitLabWebhookSecurity.apply_mr_action_filter(gitlab_mr_merge_payload, "open,reopen")
    assert dispatch is False
    assert action == "merge"


def test_apply_mr_action_filter_non_mr_dispatches_with_filter_set():
    """Non-MR events dispatch even when a filter is set — the filter is MR-only."""
    push_payload = {"object_kind": "push"}
    dispatch, action = GitLabWebhookSecurity.apply_mr_action_filter(push_payload, "open")
    assert dispatch is True and action is None


def test_verify_and_filter_raises_401_on_token_mismatch(mock_gitlab_request):
    with pytest.raises(HTTPException) as exc_info:
        GitLabWebhookSecurity.verify_and_filter(
            request=mock_gitlab_request,
            expected_token="wrong_token",
            event_filter=None,
            raw_payload=b"{}",
        )
    assert exc_info.value.status_code == 401
    assert "GitLab" in exc_info.value.detail


def test_verify_and_filter_dispatches_on_valid_token_and_allowed_action(mock_gitlab_request, gitlab_mr_open_payload):
    """Valid token + allowed MR action → dispatch."""
    import json

    mock_gitlab_request.headers["X-Gitlab-Token"] = "test_token"
    raw = json.dumps(gitlab_mr_open_payload).encode("utf-8")
    dispatch, action = GitLabWebhookSecurity.verify_and_filter(
        request=mock_gitlab_request,
        expected_token="test_token",
        event_filter="open,merge",
        raw_payload=raw,
    )
    assert dispatch is True and action is None


def test_verify_and_filter_filters_disallowed_mr_action(mock_gitlab_request, gitlab_mr_merge_payload):
    import json

    mock_gitlab_request.headers["X-Gitlab-Token"] = "test_token"
    raw = json.dumps(gitlab_mr_merge_payload).encode("utf-8")
    dispatch, action = GitLabWebhookSecurity.verify_and_filter(
        request=mock_gitlab_request,
        expected_token="test_token",
        event_filter="open,reopen",
        raw_payload=raw,
    )
    assert dispatch is False
    assert action == "merge"


def test_verify_token_valid(mock_gitlab_request):
    """Test valid GitLab token verification.

    GitLab sends the configured secret verbatim in the X-Gitlab-Token header
    (plaintext shared secret, NOT an HMAC of the body). Verification is a
    constant-time comparison of the header against the stored token.
    """
    token = "test_token"
    mock_gitlab_request.headers["X-Gitlab-Token"] = token

    result = GitLabWebhookSecurity.verify_token(mock_gitlab_request, token)
    assert result is True


def test_verify_token_invalid(mock_gitlab_request):
    """Test invalid GitLab token fails verification."""
    # The mock's X-Gitlab-Token header is "test_token_signature", which does
    # not match the expected token, so verification should fail.
    result = GitLabWebhookSecurity.verify_token(mock_gitlab_request, "correct_token")
    assert result is False


def test_verify_token_empty_expected(mock_gitlab_request):
    """Test verification fails when no expected token is configured."""
    assert GitLabWebhookSecurity.verify_token(mock_gitlab_request, "") is False


def test_verify_token_missing_header():
    """Test verification fails when the X-Gitlab-Token header is absent."""
    request = Mock()
    request.headers = {"X-Gitlab-Event": "Merge Request Hook"}
    assert GitLabWebhookSecurity.verify_token(request, "some_token") is False


def test_extract_gitlab_metadata(mock_gitlab_request):
    """Test extraction of GitLab metadata from headers."""
    metadata = GitLabWebhookSecurity.extract_gitlab_metadata(mock_gitlab_request)
    assert metadata["event_type"] == "Merge Request Hook"
    assert metadata["delivery_id"] == "12345-67890-abcdef"
    assert "GitLab" in metadata["user_agent"]
