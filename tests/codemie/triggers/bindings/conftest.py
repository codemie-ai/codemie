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

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _trigger_owner_active():
    """Schedulers and webhooks check that their owner is active, and scheduled datasource jobs load the owner at
    every run; these tests run with an active owner."""
    with (
        patch("codemie.triggers.bindings.cron.is_trigger_owner_active", return_value=True),
        patch("codemie.triggers.bindings.cron.resolve_trigger_user", side_effect=lambda user_id: MagicMock(id=user_id)),
        patch("codemie.triggers.bindings.webhook.is_trigger_owner_active", return_value=True),
    ):
        yield


def _mr_payload(action: str, state: str = "opened", **attr_overrides) -> dict:
    """Build a GitLab merge_request webhook payload for the given action.

    Mirrors the shape GitLab actually delivers (verified against GitLab 17.11):
    the action lives in ``object_attributes.action`` and there is NO top-level
    ``action`` key. Top-level keys of a real delivery are exactly
    ``changes``, ``event_type``, ``labels``, ``object_attributes``,
    ``object_kind``, ``project``, ``repository`` and ``user``.
    """
    object_attributes = {
        "id": 100,
        "iid": 1,
        "title": "Add test feature",
        "description": "Test merge request",
        "source_branch": "feature/test",
        "target_branch": "main",
        "state": state,
        "action": action,
        "url": "https://gitlab.com/group/test-project/-/merge_requests/1",
    }
    object_attributes.update(attr_overrides)
    return {
        "object_kind": "merge_request",
        "event_type": "merge_request",
        "user": {"id": 1, "name": "John Doe", "username": "johndoe"},
        "labels": [],
        "changes": {},
        "project": {
            "id": 12345,
            "name": "test-project",
            "path_with_namespace": "group/test-project",
        },
        "repository": {"name": "test-project", "homepage": "https://gitlab.com/group/test-project"},
        "object_attributes": object_attributes,
    }


def _mr_payload_top_level_action(action: str) -> dict:
    """MR payload carrying the action at the top level only.

    Not a shape GitLab sends natively, but reachable through a custom webhook
    template, so the extractor keeps supporting it as a fallback.
    """
    payload = _mr_payload(action)
    payload["object_attributes"].pop("action", None)
    payload["action"] = action
    return payload


@pytest.fixture
def gitlab_mr_open_payload():
    return _mr_payload("open", "opened")


@pytest.fixture
def gitlab_mr_close_payload():
    return _mr_payload("close", "closed")


@pytest.fixture
def gitlab_mr_merge_payload():
    return _mr_payload("merge", "merged", merge_commit_sha="xyz789", merge_user_id=1)


@pytest.fixture
def gitlab_mr_update_payload():
    return _mr_payload("update", "opened", description="Updated description")


@pytest.fixture
def gitlab_mr_reopen_payload():
    return _mr_payload("reopen", "opened")


@pytest.fixture
def gitlab_mr_approved_payload():
    return _mr_payload("approved", "opened")


@pytest.fixture
def gitlab_mr_unapproved_payload():
    return _mr_payload("unapproved", "opened")


@pytest.fixture
def gitlab_mr_open_payload_top_level_action():
    """Legacy/custom-template shape: action at the top level, not in object_attributes."""
    return _mr_payload_top_level_action("open")


@pytest.fixture
def gitlab_push_payload():
    """Non-MR event, for negative testing."""
    return {
        "object_kind": "push",
        "event_type": "push",
        "ref": "refs/heads/main",
        "project": {"id": 12345, "name": "test-project"},
    }
