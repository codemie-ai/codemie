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

"""GenericJiraIssueTool.written_refs: which issue keys a finished call wrote (EPMCDME-16835)."""

from unittest.mock import patch

import pytest

from codemie_tools.base.http_result import HttpResult


@pytest.fixture
def jira_tool(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool

        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira
        return tool


def _output(method: str, url: str, status: int, reason: str, body: str) -> str:
    return str(HttpResult(method, url, status, reason, body, "spaced"))


def test_issue_create_names_the_key_from_the_response_body(jira_tool):
    args = {"method": "POST", "relative_url": "/rest/api/2/issue"}
    output = _output("POST", "/rest/api/2/issue", 201, "Created", '{"id": "1", "key": "PROJ-12", "self": "x"}')

    assert jira_tool.written_refs(args, output) == ("PROJ-12",)


def test_issue_create_with_attachment_suffix_still_names_the_key(jira_tool):
    args = {"method": "POST", "relative_url": "/rest/api/2/issue/"}
    output = _output("POST", "/rest/api/2/issue/", 201, "Created", '{"key": "PROJ-12"}') + "\nAttachment transfer: 1/1"

    assert jira_tool.written_refs(args, output) == ("PROJ-12",)


def test_search_by_post_writes_nothing(jira_tool):
    args = {"method": "POST", "relative_url": "/rest/api/2/search"}
    output = _output("POST", "/rest/api/2/search", 200, "OK", '{"issues": [{"key": "PROJ-1"}, {"key": "PROJ-2"}]}')

    assert jira_tool.written_refs(args, output) == ()


def test_read_call_writes_nothing(jira_tool):
    args = {"method": "GET", "relative_url": "/rest/api/2/issue/PROJ-1"}

    assert (
        jira_tool.written_refs(args, _output("GET", "/rest/api/2/issue/PROJ-1", 200, "OK", '{"key": "PROJ-1"}')) == ()
    )


def test_bulk_create_names_every_created_key(jira_tool):
    args = {"method": "POST", "relative_url": "/rest/api/2/issue/bulk"}
    body = '{"issues": [{"key": "PROJ-3"}, {"key": "PROJ-4"}], "errors": []}'

    assert jira_tool.written_refs(args, _output("POST", "/rest/api/2/issue/bulk", 201, "Created", body)) == (
        "PROJ-3",
        "PROJ-4",
    )


def test_update_names_the_issue_in_the_url(jira_tool):
    args = {"method": "PUT", "relative_url": "/rest/api/2/issue/PROJ-7"}

    assert jira_tool.written_refs(args, _output("PUT", "/rest/api/2/issue/PROJ-7", 204, "No Content", "")) == (
        "PROJ-7",
    )


def test_comment_names_the_issue_not_the_comment_body_keys(jira_tool):
    args = {"method": "POST", "relative_url": "/rest/api/2/issue/PROJ-7/comment"}
    output = _output("POST", "/rest/api/2/issue/PROJ-7/comment", 201, "Created", '{"body": "see OTHER-1"}')

    assert jira_tool.written_refs(args, output) == ("PROJ-7",)


def test_create_without_a_parseable_body_cannot_tell(jira_tool):
    """A create may have created an issue even when its response cannot be read: None, not "nothing"."""
    args = {"method": "POST", "relative_url": "/rest/api/2/issue"}

    assert jira_tool.written_refs(args, "HTTP: POST /rest/api/2/issue -> 201 Created not-json") is None


def test_bulk_create_without_issues_cannot_tell(jira_tool):
    args = {"method": "POST", "relative_url": "/rest/api/2/issue/bulk"}

    assert jira_tool.written_refs(args, "HTTP: POST /rest/api/2/issue/bulk -> 201 Created {}") is None


@pytest.mark.parametrize(
    "relative_url",
    ["rest/api/2/issue", "/rest/api/latest/issue", "/rest/api/2/issue?updateHistory=true", " /rest/api/3/issue/ "],
)
def test_issue_create_url_variants_name_the_key(jira_tool, relative_url):
    args = {"method": "POST", "relative_url": relative_url}
    output = _output("POST", relative_url, 201, "Created", '{"key": "PROJ-12"}')

    assert jira_tool.written_refs(args, output) == ("PROJ-12",)


@pytest.mark.parametrize("relative_url", ["rest/api/2/issue/bulk", "/rest/api/latest/issue/bulk?x=1"])
def test_bulk_create_url_variants_name_every_key(jira_tool, relative_url):
    args = {"method": "POST", "relative_url": relative_url}
    output = _output("POST", relative_url, 201, "Created", '{"issues": [{"key": "PROJ-3"}]}')

    assert jira_tool.written_refs(args, output) == ("PROJ-3",)


@pytest.mark.parametrize("relative_url", ["rest/api/2/search", "/rest/api/latest/search/jql?x=1"])
def test_search_url_variants_write_nothing(jira_tool, relative_url):
    args = {"method": "POST", "relative_url": relative_url}
    output = _output("POST", relative_url, 200, "OK", '{"issues": [{"key": "PROJ-1"}]}')

    assert jira_tool.written_refs(args, output) == ()


def test_unrecognised_post_without_a_key_in_the_url_cannot_tell(jira_tool):
    """A POST the tool does not recognise may still have created something: None, so the caller falls back."""
    args = {"method": "POST", "relative_url": "/rest/api/2/issueLink"}

    assert jira_tool.written_refs(args, _output("POST", "/rest/api/2/issueLink", 201, "Created", "")) is None
