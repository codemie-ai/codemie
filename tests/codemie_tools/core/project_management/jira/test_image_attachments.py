# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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


import base64
from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools import (
    MAX_IMAGE_SIZE_BYTES,
    MAX_IMAGES_PER_RESPONSE,
    GenericJiraIssueTool,
    JiraMultimodalResponse,
)


@pytest.fixture
def jira_config() -> JiraConfig:
    return JiraConfig(url="https://jira.example.com", token="abc123")


@pytest.fixture
def tool(jira_config: JiraConfig) -> GenericJiraIssueTool:
    with patch("codemie_tools.core.project_management.jira.tools.Jira") as mock_cls:
        mock_jira = MagicMock()
        mock_cls.return_value = mock_jira
        with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
            t = GenericJiraIssueTool(config=jira_config)
    return t


@pytest.fixture
def issue_response_with_images() -> dict:
    return {
        "key": "TEST-123",
        "fields": {
            "summary": "Bug with login",
            "attachment": [
                {
                    "filename": "error.png",
                    "mimeType": "image/png",
                    "size": 50_000,
                    "content": "https://jira.example.com/secure/attachment/10001/error.png",
                },
                {
                    "filename": "logs.txt",
                    "mimeType": "text/plain",
                    "size": 1_000,
                    "content": "https://jira.example.com/secure/attachment/10002/logs.txt",
                },
                {
                    "filename": "screenshot.jpg",
                    "mimeType": "image/jpeg",
                    "size": 120_000,
                    "content": "https://jira.example.com/secure/attachment/10003/screenshot.jpg",
                },
            ],
        },
    }


class TestJiraMultimodalResponse:
    def test_str_returns_text(self) -> None:
        resp = JiraMultimodalResponse(text="some text", image_attachments=[{"a": 1}])
        assert str(resp) == "some text"

    def test_holds_attachments(self) -> None:
        atts = [{"filename": "x.png"}]
        resp = JiraMultimodalResponse(text="t", image_attachments=atts)
        assert resp.image_attachments == atts


class TestIsSingleIssueRequest:
    @pytest.mark.parametrize(
        "url",
        [
            "/rest/api/2/issue/TEST-123",
            "/rest/api/3/issue/PROJ-1",
            "/rest/api/2/issue/ABCDE-99999",
        ],
    )
    def test_matches_single_issue_urls(self, tool: GenericJiraIssueTool, url: str) -> None:
        assert tool._is_single_issue_request(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "/rest/api/2/issue/TEST-123/transitions",
            "/rest/api/2/issue/TEST-123/comments",
            "/rest/api/2/issue/TEST-123/attachments",
            "/rest/api/2/search",
            "/rest/api/2/myself",
            "/rest/api/2/issue/",
        ],
    )
    def test_rejects_sub_resource_and_other_urls(self, tool: GenericJiraIssueTool, url: str) -> None:
        assert tool._is_single_issue_request(url) is False


class TestExtractImageAttachments:
    def test_extracts_only_images(self, tool: GenericJiraIssueTool, issue_response_with_images: dict) -> None:
        response = MagicMock()
        response.json.return_value = issue_response_with_images

        result = tool._extract_image_attachments(response)

        assert len(result) == 2
        assert result[0]["filename"] == "error.png"
        assert result[1]["filename"] == "screenshot.jpg"

    def test_skips_oversized_images(self, tool: GenericJiraIssueTool) -> None:
        response = MagicMock()
        response.json.return_value = {
            "fields": {
                "attachment": [
                    {
                        "filename": "huge.png",
                        "mimeType": "image/png",
                        "size": MAX_IMAGE_SIZE_BYTES + 1,
                        "content": "https://example.com/huge.png",
                    },
                ]
            }
        }

        assert tool._extract_image_attachments(response) == []

    def test_caps_at_max_images(self, tool: GenericJiraIssueTool) -> None:
        attachments = [
            {
                "filename": f"img{i}.png",
                "mimeType": "image/png",
                "size": 1_000,
                "content": f"https://example.com/img{i}.png",
            }
            for i in range(10)
        ]
        response = MagicMock()
        response.json.return_value = {"fields": {"attachment": attachments}}

        result = tool._extract_image_attachments(response)
        assert len(result) == MAX_IMAGES_PER_RESPONSE

    def test_returns_empty_on_no_attachments(self, tool: GenericJiraIssueTool) -> None:
        response = MagicMock()
        response.json.return_value = {"fields": {"summary": "No attachments here"}}

        assert tool._extract_image_attachments(response) == []

    def test_returns_empty_on_json_error(self, tool: GenericJiraIssueTool) -> None:
        response = MagicMock()
        response.json.side_effect = ValueError("not JSON")

        assert tool._extract_image_attachments(response) == []


class TestDownloadAttachmentAsBase64:
    def test_successful_download(self, tool: GenericJiraIssueTool) -> None:
        image_bytes = b"\x89PNG\r\n\x1a\n fake image data"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = image_bytes
        tool.jira.request.return_value = mock_response

        result = tool._download_attachment_as_base64("https://jira.example.com/att/1/img.png")

        assert result == base64.b64encode(image_bytes).decode("utf-8")
        tool.jira.request.assert_called_once_with(
            method="GET",
            path="https://jira.example.com/att/1/img.png",
            advanced_mode=True,
            absolute=True,
        )

    def test_returns_none_on_http_error(self, tool: GenericJiraIssueTool) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.content = b""
        tool.jira.request.return_value = mock_response

        assert tool._download_attachment_as_base64("https://jira.example.com/att/1/img.png") is None

    def test_returns_none_on_exception(self, tool: GenericJiraIssueTool) -> None:
        tool.jira.request.side_effect = ConnectionError("timeout")

        assert tool._download_attachment_as_base64("https://jira.example.com/att/1/img.png") is None


class TestDownloadImageArtifacts:
    def test_downloads_all_successfully(self, tool: GenericJiraIssueTool) -> None:
        image_bytes = b"fake-png"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = image_bytes
        tool.jira.request.return_value = mock_response

        attachments = [
            {"filename": "a.png", "content_url": "https://ex.com/a.png", "mime_type": "image/png", "size": 100},
            {"filename": "b.jpg", "content_url": "https://ex.com/b.jpg", "mime_type": "image/jpeg", "size": 200},
        ]

        result = tool._download_image_artifacts(attachments)

        assert result is not None
        assert len(result) == 2
        assert result[0]["filename"] == "a.png"
        assert result[0]["mime_type"] == "image/png"
        assert result[0]["data"] == base64.b64encode(image_bytes).decode("utf-8")

    def test_skips_failed_downloads(self, tool: GenericJiraIssueTool) -> None:
        ok_response = MagicMock(status_code=200, content=b"ok-image")
        fail_response = MagicMock(status_code=404, content=b"")
        tool.jira.request.side_effect = [ok_response, fail_response]

        attachments = [
            {"filename": "good.png", "content_url": "https://ex.com/good.png", "mime_type": "image/png", "size": 100},
            {"filename": "bad.png", "content_url": "https://ex.com/bad.png", "mime_type": "image/png", "size": 100},
        ]

        result = tool._download_image_artifacts(attachments)

        assert result is not None
        assert len(result) == 1
        assert result[0]["filename"] == "good.png"

    def test_returns_none_when_all_fail(self, tool: GenericJiraIssueTool) -> None:
        tool.jira.request.side_effect = ConnectionError("network down")

        attachments = [
            {"filename": "a.png", "content_url": "https://ex.com/a.png", "mime_type": "image/png", "size": 100},
        ]

        assert tool._download_image_artifacts(attachments) is None


class TestPostProcessOutputContent:
    def test_returns_tuple_with_artifacts_when_images_present(self, tool: GenericJiraIssueTool) -> None:
        image_bytes = b"png-data"
        mock_response = MagicMock(status_code=200, content=image_bytes)
        tool.jira.request.return_value = mock_response

        output = JiraMultimodalResponse(
            text="HTTP: GET ... 200 OK",
            image_attachments=[
                {"filename": "x.png", "content_url": "https://ex.com/x.png", "mime_type": "image/png", "size": 100},
            ],
        )

        result = tool._post_process_output_content(output)

        assert isinstance(result, tuple)
        assert len(result) == 2
        content, artifact = result
        assert isinstance(content, str)
        assert "HTTP: GET" in content
        assert isinstance(artifact, list)
        assert artifact[0]["filename"] == "x.png"

    def test_returns_tuple_with_none_for_no_images(self, tool: GenericJiraIssueTool) -> None:
        output = JiraMultimodalResponse(text="HTTP: GET ... 200 OK", image_attachments=[])

        result = tool._post_process_output_content(output)

        assert isinstance(result, tuple)
        content, artifact = result
        assert "HTTP: GET" in content
        assert artifact is None

    def test_returns_tuple_with_none_for_plain_string(self, tool: GenericJiraIssueTool) -> None:
        result = tool._post_process_output_content("plain string response")

        assert isinstance(result, tuple)
        content, artifact = result
        assert content == "plain string response"
        assert artifact is None

    def test_returns_none_artifact_when_all_downloads_fail(self, tool: GenericJiraIssueTool) -> None:
        tool.jira.request.side_effect = ConnectionError("down")

        output = JiraMultimodalResponse(
            text="HTTP: GET ... 200 OK",
            image_attachments=[
                {"filename": "x.png", "content_url": "https://ex.com/x.png", "mime_type": "image/png", "size": 100},
            ],
        )

        content, artifact = tool._post_process_output_content(output)
        assert isinstance(content, str)
        assert artifact is None


class TestLimitOutputContent:
    def test_preserves_image_attachments_after_truncation(self, tool: GenericJiraIssueTool) -> None:
        atts = [{"filename": "img.png", "content_url": "https://ex.com/img.png", "mime_type": "image/png", "size": 1}]
        output = JiraMultimodalResponse(text="short text", image_attachments=atts)

        result, token_count = tool._limit_output_content(output)

        assert isinstance(result, JiraMultimodalResponse)
        assert result.image_attachments == atts
        assert "short text" in result.text

    def test_passes_string_to_base_class(self, tool: GenericJiraIssueTool) -> None:
        result, _ = tool._limit_output_content("regular string")
        assert result == "regular string"
