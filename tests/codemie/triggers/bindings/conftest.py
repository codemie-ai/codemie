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


def _mr_payload(action: str, state: str = "opened", **attr_overrides) -> dict:
    """Build a GitLab merge_request webhook payload for the given action."""
    object_attributes = {
        "id": 100,
        "iid": 1,
        "title": "Add test feature",
        "description": "Test merge request",
        "source_branch": "feature/test",
        "target_branch": "main",
        "state": state,
        "url": "https://gitlab.com/group/test-project/-/merge_requests/1",
    }
    object_attributes.update(attr_overrides)
    return {
        "object_kind": "merge_request",
        "action": action,
        "event_type": "merge_request",
        "user": {"id": 1, "name": "John Doe", "username": "johndoe"},
        "project": {
            "id": 12345,
            "name": "test-project",
            "path_with_namespace": "group/test-project",
        },
        "object_attributes": object_attributes,
    }


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
def gitlab_push_payload():
    """Non-MR event, for negative testing."""
    return {
        "object_kind": "push",
        "event_type": "push",
        "ref": "refs/heads/main",
        "project": {"id": 12345, "name": "test-project"},
    }
