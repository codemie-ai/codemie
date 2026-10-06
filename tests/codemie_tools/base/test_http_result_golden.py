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

"""Golden tests: HTTP tools keep their byte-exact `execute()` strings and expose status/body to scripts."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools.base import ToolException

from codemie.agents.tools.kb.search_kb import SearchKBResponse, SearchKBTool
from codemie.rest_api.models.index import IndexInfo
from codemie_tools.base.http_result import HttpResult
from codemie_tools.base.script_result import ScriptResult, ScriptResultSource
from codemie_tools.core.project_management.confluence.models import ConfluenceConfig
from codemie_tools.core.project_management.confluence.tools import GenericConfluenceTool
from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool, JiraMultimodalResponse
from codemie_tools.core.project_management.xwiki.models import XWikiConfig
from codemie_tools.core.project_management.xwiki.tools import GetPageTool
from codemie_tools.core.vcs.gitlab.models import GitlabConfig
from codemie_tools.core.vcs.gitlab.tools import GitlabTool

JSON_BODY = '{"id": 1, "name": "test"}'
PARSED_BODY = {"id": 1, "name": "test"}


def _requests_response(status: int, reason: str, text: str) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.reason = reason
    response.text = text
    return response


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


@pytest.fixture
def jira_tool() -> GenericJiraIssueTool:
    config = JiraConfig(url="https://jira.example.com", token="abc123")
    with patch("codemie_tools.core.project_management.jira.tools.Jira") as jira_cls:
        jira_cls.return_value = MagicMock()
        with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
            tool = GenericJiraIssueTool(config=config)
            tool._ensure_client()
    return tool


class TestJiraGolden:
    def test_get_execute_string_is_byte_exact(self, jira_tool: GenericJiraIssueTool) -> None:
        jira_tool.jira.request.return_value = _requests_response(200, "OK", JSON_BODY)

        result = jira_tool.execute(method="GET", relative_url="/rest/api/2/myself")

        assert str(result) == 'HTTP: GET /rest/api/2/myself -> 200 OK {"id": 1, "name": "test"}'

    def test_get_execute_structured_gives_parsed_body_and_status(self, jira_tool: GenericJiraIssueTool) -> None:
        jira_tool.jira.request.return_value = _requests_response(200, "OK", JSON_BODY)

        result = jira_tool.execute_structured(method="GET", relative_url="/rest/api/2/myself")

        assert result == ScriptResult(result=PARSED_BODY, http_status=200, http_reason="OK")
        assert isinstance(result.http_status, int)

    def test_attachment_transfer_suffix_is_kept_in_string_and_body_stays_clean(
        self, jira_tool: GenericJiraIssueTool
    ) -> None:
        created = '{"key": "NEW-1"}'
        jira_tool.jira.request.return_value = _requests_response(201, "Created", created)
        copy_results = [{"status": "copied", "ocr_text": "text"}, {"status": "failed"}]
        params = json.dumps({"source_issue_key": "OLD-1", "copy_attachments": True, "ocr_images": True})

        with patch.object(GenericJiraIssueTool, "copy_attachments_from_issue", return_value=copy_results):
            result = jira_tool.execute(method="POST", relative_url="/rest/api/2/issue", params=params)

        assert str(result) == (
            'HTTP: POST /rest/api/2/issue -> 201 Created {"key": "NEW-1"}'
            "\nAttachment transfer: 1/2 copied. 1 image(s) OCR'd."
        )
        assert isinstance(result, HttpResult)
        assert result.body == created
        assert result.to_script_result().result == {"key": "NEW-1"}

    def test_multimodal_response_string_is_unchanged_and_script_value_is_text_only(
        self, jira_tool: GenericJiraIssueTool
    ) -> None:
        jira_tool.jira.request.return_value = _requests_response(200, "OK", JSON_BODY)
        images = [{"filename": "a.png", "mime_type": "image/png", "size": 10, "content_url": "https://x/a.png"}]

        with patch.object(GenericJiraIssueTool, "_extract_image_attachments", return_value=images):
            result = jira_tool.execute(method="GET", relative_url="/rest/api/2/issue/TEST-1")
            structured = jira_tool.execute_structured(method="GET", relative_url="/rest/api/2/issue/TEST-1")

        assert isinstance(result, JiraMultimodalResponse)
        assert str(result) == 'HTTP: GET /rest/api/2/issue/TEST-1 -> 200 OK {"id": 1, "name": "test"}'
        assert result.text == str(result)
        assert result.image_attachments == images
        assert isinstance(result, ScriptResultSource)
        assert result.to_script_result() == ScriptResult(result=PARSED_BODY, http_status=200, http_reason="OK")
        # The value is the parsed body without images; the http block matches the non-image case.
        assert structured == ScriptResult(result=PARSED_BODY, http_status=200, http_reason="OK")

    def test_multimodal_response_without_http_result_has_no_status(self) -> None:
        response = JiraMultimodalResponse(text="plain", image_attachments=[])

        assert response.to_script_result() == ScriptResult(result="plain")

    def test_script_path_keeps_a_404_as_data_with_its_status(self, jira_tool: GenericJiraIssueTool) -> None:
        missing = '{"errorMessages": ["Issue Does Not Exist"]}'
        jira_tool.jira.request.return_value = _requests_response(404, "Not Found", missing)
        jira_tool.jira.raise_for_status.side_effect = RuntimeError("404 Client Error")

        result = jira_tool.execute_structured(method="GET", relative_url="/rest/api/2/issue/NOPE-1")

        assert result == ScriptResult(
            result={"errorMessages": ["Issue Does Not Exist"]}, http_status=404, http_reason="Not Found"
        )
        jira_tool.jira.raise_for_status.assert_not_called()

    def test_script_path_keeps_a_non_get_error_as_data(self, jira_tool: GenericJiraIssueTool) -> None:
        jira_tool.jira.request.return_value = _requests_response(400, "Bad Request", '{"errors": {"summary": "x"}}')
        jira_tool.jira.raise_for_status.side_effect = RuntimeError("400 Client Error")

        result = jira_tool.execute_structured(method="POST", relative_url="/rest/api/2/issue", params="{}")

        assert result.http_status == 400
        assert result.result == {"errors": {"summary": "x"}}

    def test_model_path_still_raises_on_non_2xx_and_the_script_flag_does_not_leak(
        self, jira_tool: GenericJiraIssueTool
    ) -> None:
        jira_tool.jira.request.return_value = _requests_response(404, "Not Found", "{}")
        jira_tool.jira.raise_for_status.side_effect = RuntimeError("404 Client Error")

        jira_tool.execute_structured(method="GET", relative_url="/rest/api/2/issue/NOPE-1")
        with pytest.raises(RuntimeError, match="404 Client Error"):
            jira_tool.execute(method="GET", relative_url="/rest/api/2/issue/NOPE-1")
        with pytest.raises(ToolException, match="404 Client Error"):
            jira_tool._run(method="GET", relative_url="/rest/api/2/issue/NOPE-1")

    def test_multimodal_response_survives_token_limiting_copy(self, jira_tool: GenericJiraIssueTool) -> None:
        jira_tool.jira.request.return_value = _requests_response(200, "OK", JSON_BODY)
        images = [{"filename": "a.png", "mime_type": "image/png", "size": 10, "content_url": "https://x/a.png"}]

        with patch.object(GenericJiraIssueTool, "_extract_image_attachments", return_value=images):
            result = jira_tool.execute(method="GET", relative_url="/rest/api/2/issue/TEST-1")

        limited, _ = jira_tool._limit_output_content(result)
        assert isinstance(limited, JiraMultimodalResponse)
        assert limited.text == str(result)
        assert limited.image_attachments == images


# ---------------------------------------------------------------------------
# Confluence
# ---------------------------------------------------------------------------


class TestConfluenceGolden:
    @pytest.fixture
    def confluence_tool(self) -> tuple[GenericConfluenceTool, MagicMock]:
        config = ConfluenceConfig(url="https://confluence.example.com", token="abc", username="u", cloud=False)
        client = MagicMock()
        with patch("codemie_tools.core.project_management.confluence.tools.Confluence", return_value=client):
            with patch("codemie_tools.core.project_management.confluence.tools.validate_creds"):
                tool = GenericConfluenceTool(config=config)
                yield tool, client

    def test_get_execute_string_is_byte_exact(self, confluence_tool: tuple[GenericConfluenceTool, MagicMock]) -> None:
        tool, client = confluence_tool
        client.request.return_value = _requests_response(200, "OK", JSON_BODY)

        result = tool.execute(method="GET", relative_url="/rest/api/content/1")

        assert str(result) == 'HTTP: GET/rest/api/content/1 -> 200OK{"id": 1, "name": "test"}'
        assert tool.execute_structured(method="GET", relative_url="/rest/api/content/1") == ScriptResult(
            result=PARSED_BODY, http_status=200, http_reason="OK"
        )

    def test_404_is_data_not_an_error(self, confluence_tool: tuple[GenericConfluenceTool, MagicMock]) -> None:
        tool, client = confluence_tool
        client.request.return_value = _requests_response(404, "Not Found", '{"message": "gone"}')

        result = tool.execute(method="GET", relative_url="/rest/api/content/9")
        structured = tool.execute_structured(method="GET", relative_url="/rest/api/content/9")

        assert str(result) == 'HTTP: GET/rest/api/content/9 -> 404Not Found{"message": "gone"}'
        assert structured == ScriptResult(result={"message": "gone"}, http_status=404, http_reason="Not Found")


class TestToolsAreSafeForParallelScriptCalls:
    """A script may make several calls at once on the same tool instance (`call_tools`)."""

    def test_two_first_calls_to_the_jira_tool_create_its_client_once(self) -> None:
        import threading
        import time

        config = JiraConfig(url="https://jira.example.com", token="abc123")
        created: list[int] = []

        def slow_create(self: GenericJiraIssueTool) -> MagicMock:
            created.append(1)
            time.sleep(0.2)
            return MagicMock()

        with patch.object(GenericJiraIssueTool, "_create_client", slow_create):
            tool = GenericJiraIssueTool(config=config)
            clients: list[object] = []
            start = threading.Barrier(2)

            def first_call() -> None:
                start.wait()
                clients.append(tool._ensure_client())

            threads = [threading.Thread(target=first_call) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        assert len(created) == 1
        assert len(clients) == 2 and clients[0] is clients[1]

    def test_the_confluence_script_path_never_changes_the_tool_state(
        self,
    ) -> None:
        config = ConfluenceConfig(url="https://confluence.example.com", token="abc", username="u", cloud=False)
        client = MagicMock()
        client.request.return_value = _requests_response(200, "OK", "<p>page</p>")
        with patch("codemie_tools.core.project_management.confluence.tools.Confluence", return_value=client):
            with patch("codemie_tools.core.project_management.confluence.tools.validate_creds"):
                tool = GenericConfluenceTool(config=config)
                before = tool.tokens_size_limit

                tool.run_for_script({"method": "GET", "relative_url": "/rest/api/content/12345"})
                after_script = tool.tokens_size_limit
                tool.execute(method="GET", relative_url="/rest/api/content/12345")
                after_model = tool.tokens_size_limit

        assert after_script == before, "a script call must not write to the shared tool instance"
        assert after_model == 20000, "the model path keeps its smaller output limit for a page read"


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


class TestGitlabGolden:
    @pytest.fixture
    def gitlab_tool(self) -> GitlabTool:
        return GitlabTool(config=GitlabConfig(url="https://gitlab.example.com", token="tok"))

    def test_execute_string_is_byte_exact(self, gitlab_tool: GitlabTool) -> None:
        query = {"method": "GET", "url": "/api/v4/projects", "method_arguments": {}}
        with patch("requests.request", return_value=_requests_response(200, "OK", JSON_BODY)):
            result = gitlab_tool.execute(query)

        assert (
            str(result) == 'HTTP: GET https://gitlab.example.com//api/v4/projects -> 200 OK {"id": 1, "name": "test"}'
        )

    def test_execute_structured_gives_parsed_body_and_status(self, gitlab_tool: GitlabTool) -> None:
        query = {"method": "GET", "url": "/api/v4/projects", "method_arguments": {}}
        with patch("requests.request", return_value=_requests_response(200, "OK", JSON_BODY)):
            structured = gitlab_tool.execute_structured(query)

        assert structured == ScriptResult(result=PARSED_BODY, http_status=200, http_reason="OK")

    def test_404_is_data(self, gitlab_tool: GitlabTool) -> None:
        query = {"method": "GET", "url": "/api/v4/nope", "method_arguments": {}}
        with patch("requests.request", return_value=_requests_response(404, "Not Found", '{"message": "404"}')):
            structured = gitlab_tool.execute_structured(query)

        assert structured == ScriptResult(result={"message": "404"}, http_status=404, http_reason="Not Found")


# ---------------------------------------------------------------------------
# xWiki
# ---------------------------------------------------------------------------


class TestXWikiGolden:
    @pytest.fixture
    def xwiki_tool(self) -> GetPageTool:
        return GetPageTool(config=XWikiConfig(url="https://wiki.example.com", token="tok", username="u"))

    @staticmethod
    def _http_response(status: int, reason: str) -> MagicMock:
        response = MagicMock()
        response.status_code = status
        response.reason_phrase = reason
        return response

    def test_execute_string_is_byte_exact(self, xwiki_tool: GetPageTool) -> None:
        xwiki_tool._request = MagicMock(return_value=(self._http_response(200, "OK"), JSON_BODY))

        result = xwiki_tool.execute(wiki="xwiki", space="Main", page="WebHome")

        assert str(result) == (
            "HTTP: GET https://wiki.example.com/rest/wikis/xwiki/spaces/Main/pages/WebHome -> 200 OK\n" + JSON_BODY
        )

    def test_execute_structured_gives_parsed_body_and_status(self, xwiki_tool: GetPageTool) -> None:
        xwiki_tool._request = MagicMock(return_value=(self._http_response(200, "OK"), JSON_BODY))

        structured = xwiki_tool.execute_structured(wiki="xwiki", space="Main", page="WebHome")

        assert structured == ScriptResult(result=PARSED_BODY, http_status=200, http_reason="OK")

    def test_404_is_data(self, xwiki_tool: GetPageTool) -> None:
        xwiki_tool._request = MagicMock(return_value=(self._http_response(404, "Not Found"), "missing"))

        structured = xwiki_tool.execute_structured(wiki="xwiki", space="Main", page="Nope")

        assert structured == ScriptResult(result="missing", http_status=404, http_reason="Not Found")


# ---------------------------------------------------------------------------
# Search KB
# ---------------------------------------------------------------------------


class TestSearchKBResponseScriptValue:
    def test_to_script_result_is_text_only(self) -> None:
        response = SearchKBResponse(text="kb text", image_artifacts=[{"data": "b64", "mime_type": "image/png"}])

        assert isinstance(response, ScriptResultSource)
        assert response.to_script_result() == ScriptResult(result="kb text")
        assert str(response) == "kb text"

    def test_execute_structured_has_no_images(self) -> None:
        kb_index = MagicMock(spec=IndexInfo)
        kb_index.index_type = "llm_routing_google"
        kb_index.repo_name = "kb"
        tool = SearchKBTool.model_construct(
            index_info=kb_index, llm_model="gpt-4", metadata={}, tokens_size_limit=20000
        )
        response = SearchKBResponse(text="routed text", image_artifacts=[{"data": "b64", "mime_type": "image/png"}])

        with patch.object(SearchKBTool, "execute", return_value=response):
            structured = tool.execute_structured(query="q")

        assert structured == ScriptResult(result="routed text")
