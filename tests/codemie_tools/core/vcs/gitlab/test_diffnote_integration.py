# Copyright 2026 EPAM Systems, Inc. (â€œEPAMâ€)
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

import json
import os

import pytest

from codemie_tools.core.vcs.gitlab.models import GitlabConfig
from codemie_tools.core.vcs.gitlab.tools import GitlabTool


_REQUIRED_ENVIRONMENT = (
    "GITLAB_DIFFNOTE_BASE_URL",
    "GITLAB_DIFFNOTE_TOKEN",
    "GITLAB_DIFFNOTE_PROJECT_ID",
    "GITLAB_DIFFNOTE_MR_IID",
    "GITLAB_DIFFNOTE_BASE_SHA",
    "GITLAB_DIFFNOTE_START_SHA",
    "GITLAB_DIFFNOTE_HEAD_SHA",
    "GITLAB_DIFFNOTE_NEW_PATH",
    "GITLAB_DIFFNOTE_NEW_LINE",
)
_MISSING_ENVIRONMENT = [name for name in _REQUIRED_ENVIRONMENT if not os.getenv(name)]


@pytest.mark.skipif(
    _MISSING_ENVIRONMENT,
    reason=(
        "Set the GitLab DiffNote validation variables documented in "
        "docs/superpowers/tasks/2026-09-18-EPMCDME-13495/inline-diffnote-validation.md"
    ),
)
def test_gitlab_tool_creates_inline_diffnote():
    """Validate that GitLab accepts a nested position and creates a DiffNote."""
    project_id = os.environ["GITLAB_DIFFNOTE_PROJECT_ID"]
    merge_request_iid = os.environ["GITLAB_DIFFNOTE_MR_IID"]
    new_path = os.environ["GITLAB_DIFFNOTE_NEW_PATH"]
    new_line = int(os.environ["GITLAB_DIFFNOTE_NEW_LINE"])
    url = f"/api/v4/projects/{project_id}/merge_requests/{merge_request_iid}/discussions"
    position = {
        "base_sha": os.environ["GITLAB_DIFFNOTE_BASE_SHA"],
        "start_sha": os.environ["GITLAB_DIFFNOTE_START_SHA"],
        "head_sha": os.environ["GITLAB_DIFFNOTE_HEAD_SHA"],
        "position_type": "text",
        "new_path": new_path,
        "new_line": new_line,
    }
    tool = GitlabTool(
        config=GitlabConfig(
            url=os.environ["GITLAB_DIFFNOTE_BASE_URL"].rstrip("/"),
            token=os.environ["GITLAB_DIFFNOTE_TOKEN"],
        )
    )

    result = tool.execute(
        {
            "method": "POST",
            "url": url,
            "method_arguments": {
                "body": os.getenv("GITLAB_DIFFNOTE_BODY", "EPMCDME-13495 JSON body validation"),
                "position": position,
            },
        }
    )

    assert " -> 201 Created " in result
    response_body = json.loads(result.split(" -> 201 Created ", 1)[1])
    notes = response_body.get("notes", [response_body])
    diff_note = next((note for note in notes if note.get("type") == "DiffNote"), None)

    assert diff_note is not None
    assert diff_note["position"]["new_path"] == new_path
    assert diff_note["position"]["new_line"] == new_line
    assert diff_note["position"]["base_sha"] == position["base_sha"]
    assert diff_note["position"]["start_sha"] == position["start_sha"]
    assert diff_note["position"]["head_sha"] == position["head_sha"]
