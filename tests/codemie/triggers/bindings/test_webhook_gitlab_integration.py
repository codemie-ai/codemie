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
from unittest.mock import Mock, patch
from fastapi import HTTPException

from codemie.triggers.bindings.webhook import WebhookService
from codemie.service.monitoring.webhook_monitoring_service import WebhookMonitoringService


class _FakeSetting:
    """Minimal stand-in for a Settings object in verify_security_header."""

    def __init__(self, creds: dict):
        self._creds = creds
        self.project_name = "test-project"
        self.user_id = "user-1"
        self.alias = "gitlab-hook"

    def credential(self, key):
        return self._creds.get(key)


def _gitlab_request(token_header: str):
    request = Mock()
    request.headers = {
        "X-Gitlab-Event": "Merge Request Hook",
        "X-Gitlab-Delivery": "abc-123",
        "X-Gitlab-Token": token_header,
        "User-Agent": "GitLab/16.0",
    }
    return request


@pytest.fixture(autouse=True)
def _silence_metrics():
    with patch.object(WebhookMonitoringService, "send_webhook_invocation_metric", return_value=None):
        yield


def test_gitlab_allowed_action_passes():
    setting = _FakeSetting(
        {
            "webhook_id": "w1",
            "gitlab_webhook_token": "tok",
            "gitlab_event_filter": "open,merge",
        }
    )
    request = _gitlab_request("tok")
    raw = b'{"object_kind":"merge_request","action":"open"}'
    # Allowed action -> signals dispatch (True), no raise.
    assert WebhookService.verify_security_header(request, setting, raw) is True


def test_gitlab_filtered_action_signals_skip_without_raising():
    """A filtered-out MR action must ACK with 200 (return False), not raise 4xx.

    GitLab auto-deactivates webhook endpoints after repeated non-2xx responses,
    so a routine filter mismatch cannot surface as an error to the caller.
    """
    setting = _FakeSetting(
        {
            "webhook_id": "w1",
            "gitlab_webhook_token": "tok",
            "gitlab_event_filter": "open,merge",
        }
    )
    request = _gitlab_request("tok")
    raw = b'{"object_kind":"merge_request","action":"update"}'
    assert WebhookService.verify_security_header(request, setting, raw) is False


def test_gitlab_non_mr_event_with_filter_passes_through():
    """Non-MR events (push, pipeline, tag) are outside the MR filter's scope
    and must dispatch normally even when the filter is set — a single webhook
    URL may legitimately receive multiple event types."""
    setting = _FakeSetting(
        {
            "webhook_id": "w1",
            "gitlab_webhook_token": "tok",
            "gitlab_event_filter": "open,merge",
        }
    )
    request = _gitlab_request("tok")
    request.headers["X-Gitlab-Event"] = "Push Hook"
    raw = b'{"object_kind":"push"}'
    assert WebhookService.verify_security_header(request, setting, raw) is True


def test_gitlab_invalid_token_raises_401():
    setting = _FakeSetting({"webhook_id": "w1", "gitlab_webhook_token": "tok"})
    request = _gitlab_request("WRONG")
    with pytest.raises(HTTPException) as exc:
        WebhookService.verify_security_header(request, setting, b"{}")
    assert exc.value.status_code == 401


def test_gitlab_empty_filter_allows_all():
    setting = _FakeSetting({"webhook_id": "w1", "gitlab_webhook_token": "tok"})
    request = _gitlab_request("tok")
    raw = b'{"object_kind":"merge_request","action":"update"}'
    # No filter configured -> allow all -> must NOT raise.
    WebhookService.verify_security_header(request, setting, raw)


def test_gitlab_without_configured_token_falls_through_to_legacy():
    """Backward compat: GitLab-looking request but no gitlab_webhook_token
    configured must fall through to legacy-header auth, not force a 401."""
    setting = _FakeSetting(
        {
            "webhook_id": "w1",
            "secure_header_name": "X-Secure",
            "secure_header_value": "expected",
        }
    )
    request = _gitlab_request("irrelevant")
    request.headers["X-Secure"] = "expected"
    # Legacy header matches -> must NOT raise.
    WebhookService.verify_security_header(request, setting, b"{}")


@pytest.mark.parametrize(
    "fixture_name, expected_action",
    [
        ("gitlab_mr_open_payload", "open"),
        ("gitlab_mr_close_payload", "close"),
        ("gitlab_mr_merge_payload", "merge"),
        ("gitlab_mr_update_payload", "update"),
        ("gitlab_mr_reopen_payload", "reopen"),
        ("gitlab_mr_approved_payload", "approved"),
        ("gitlab_mr_unapproved_payload", "unapproved"),
    ],
)
def test_all_mr_actions_extracted_and_self_filter(request, fixture_name, expected_action):
    from codemie.triggers.bindings.gitlab_webhook_security import GitLabWebhookSecurity

    payload = request.getfixturevalue(fixture_name)
    assert GitLabWebhookSecurity.extract_mr_action(payload) == expected_action
    # A filter containing exactly this action allows it...
    assert GitLabWebhookSecurity.validate_mr_event_type(payload, [expected_action]) is True
    # ...and a filter without it rejects it.
    others = [a for a in GitLabWebhookSecurity.MR_ACTIONS if a != expected_action]
    assert GitLabWebhookSecurity.validate_mr_event_type(payload, others) is False


def test_push_event_is_not_an_mr_action(gitlab_push_payload):
    from codemie.triggers.bindings.gitlab_webhook_security import GitLabWebhookSecurity

    assert GitLabWebhookSecurity.extract_mr_action(gitlab_push_payload) is None
    # Non-MR events (push, pipeline, tag, note, …) are outside the MR-action
    # filter's scope and pass through so a single webhook URL can receive
    # multiple event types.
    assert GitLabWebhookSecurity.validate_mr_event_type(gitlab_push_payload, ["open"]) is True
