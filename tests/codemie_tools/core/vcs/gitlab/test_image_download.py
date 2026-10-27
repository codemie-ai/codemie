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


"""Tests for GitLab tool image downloads returned as multimodal artifacts (EPMCDME-14273)."""

import base64
from unittest.mock import Mock, patch

import pytest
import requests

from codemie_tools.core.vcs.gitlab.tools import GitlabMultimodalResponse, GitlabTool
from codemie_tools.core.vcs.gitlab.tools_vars import GITLAB_TOOL

ARTIFACT = {"data": "aGVsbG8=", "mime_type": "image/png"}


def _container() -> GitlabMultimodalResponse:
    return GitlabMultimodalResponse(text="HTTP: GET url -> 200 OK Image downloaded", image_artifacts=[ARTIFACT])


class TestTupleContract:
    def test_response_format_is_content_and_artifact(self, gitlab_tool):
        assert gitlab_tool.response_format == "content_and_artifact"
        assert GitlabTool.model_fields["response_format"].default == "content_and_artifact"

    def test_post_process_container_returns_text_and_artifacts(self, gitlab_tool):
        text, artifacts = gitlab_tool._post_process_output_content(_container())
        assert text == "HTTP: GET url -> 200 OK Image downloaded"
        assert artifacts == [ARTIFACT]

    def test_post_process_plain_string_returns_none_artifact(self, gitlab_tool):
        assert gitlab_tool._post_process_output_content("plain") == ("plain", None)

    def test_limit_output_content_keeps_container_and_artifacts(self, gitlab_tool):
        limited, token_count = gitlab_tool._limit_output_content(_container())
        assert isinstance(limited, GitlabMultimodalResponse)
        assert limited.image_artifacts == [ARTIFACT]
        assert token_count > 0

    def test_container_str_and_script_result_hold_no_base64(self):
        container = _container()
        assert str(container) == container.text
        assert ARTIFACT["data"] not in str(container)
        script_result = container.to_script_result()
        assert script_result.result == container.text
        assert ARTIFACT["data"] not in str(script_result.result)

    @patch("requests.request")
    def test_invoke_non_image_returns_tuple_with_none(self, mock_request, gitlab_tool, mock_response):
        mock_request.return_value = mock_response
        query = {"method": "GET", "url": "/api/v4/projects", "method_arguments": {}}

        expected = str(gitlab_tool.execute(query))
        result = gitlab_tool._run(query=query)

        assert isinstance(result, tuple) and len(result) == 2
        assert result[0] == expected
        assert result[1] is None

    @patch("requests.request")
    def test_invoke_with_tool_call_yields_message_without_artifact(self, mock_request, gitlab_tool, mock_response):
        mock_request.return_value = mock_response
        query = {"method": "GET", "url": "/api/v4/projects", "method_arguments": {}}

        message = gitlab_tool.invoke({"name": "gitlab", "args": {"query": query}, "id": "call-1", "type": "tool_call"})

        assert message.artifact is None
        assert '{"id": 1, "name": "test"}' in message.content


class TestSameOriginUrls:
    @patch("requests.request")
    def test_same_origin_absolute_url_is_requested_as_is(self, mock_request, gitlab_tool, mock_response):
        mock_request.return_value = mock_response
        query = {"method": "GET", "url": "https://gitlab.example.com/api/v4/projects?x=1", "method_arguments": {}}

        gitlab_tool.execute(query)

        assert mock_request.call_args.kwargs["url"] == "https://gitlab.example.com/api/v4/projects?x=1"

    @pytest.mark.parametrize(
        "foreign_url",
        [
            "https://evil.com/api/v4/projects",
            "http://gitlab.example.com/api/v4/projects",
            "https://gitlab.example.com:8443/api/v4/projects",
        ],
    )
    @patch("requests.request")
    def test_foreign_origin_absolute_url_not_requested(self, mock_request, gitlab_tool, foreign_url):
        query = {"method": "GET", "url": foreign_url, "method_arguments": {}}

        result = gitlab_tool.execute(query)

        mock_request.assert_not_called()
        assert "origin" in str(result)
        assert foreign_url.split("/api/")[0] in str(result)

    @pytest.mark.parametrize("relative_url", ["/api/v4/projects", "api/v4/projects", "//api/v4/projects"])
    @patch("requests.request")
    def test_relative_url_is_joined_with_single_slash(self, mock_request, gitlab_tool, mock_response, relative_url):
        mock_request.return_value = mock_response
        query = {"method": "GET", "url": relative_url, "method_arguments": {}}

        gitlab_tool.execute(query)

        assert mock_request.call_args.kwargs["url"] == "https://gitlab.example.com/api/v4/projects"

    @patch("requests.request")
    def test_configured_url_trailing_slash_is_not_doubled(self, mock_request, gitlab_tool, mock_response):
        mock_request.return_value = mock_response
        gitlab_tool.config.url = "https://gitlab.example.com/"
        query = {"method": "GET", "url": "/api/v4/projects", "method_arguments": {}}

        gitlab_tool.execute(query)

        assert mock_request.call_args.kwargs["url"] == "https://gitlab.example.com/api/v4/projects"


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32
BASE = "https://gitlab.example.com"
UPLOAD_PATH = "/group/proj/uploads/abc"


def _image_response(body: bytes = PNG_BYTES, headers: dict | None = None, status_code: int = 200) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.reason = "OK" if status_code < 400 else "Not Found"
    response.headers = {"Content-Type": "image/png"} if headers is None else headers
    response.iter_content = Mock(side_effect=lambda chunk_size: iter([body[i : i + 7] for i in range(0, len(body), 7)]))
    response.text = '{"message": "404 Not Found"}' if status_code >= 400 else body.decode("latin-1")
    return response


def _get(url: str) -> dict:
    return {"method": "GET", "url": url, "method_arguments": {}}


class TestImageDownload:
    @patch("requests.request")
    def test_image_content_type_returns_artifact(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response()

        result = gitlab_tool.execute(_get(f"{UPLOAD_PATH}/x.png"))

        assert isinstance(result, GitlabMultimodalResponse)
        assert len(result.image_artifacts) == 1
        artifact = result.image_artifacts[0]
        assert artifact["mime_type"] == "image/png"
        assert base64.b64decode(artifact["data"]) == PNG_BYTES
        assert "Image downloaded (image/png, 40 bytes)" in result.text
        assert result.text.startswith("HTTP: GET ")
        assert "-> 200 OK" in result.text
        assert artifact["data"] not in result.text

    @patch("requests.request")
    def test_run_yields_text_and_artifact_tuple(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response()

        text, artifacts = gitlab_tool._run(query=_get("/api/v4/projects/1/uploads/s/x.png"))

        assert "Image downloaded" in text
        assert base64.b64decode(artifacts[0]["data"]) == PNG_BYTES

    @patch("requests.request")
    def test_get_is_streamed(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response()

        gitlab_tool.execute(_get(f"{UPLOAD_PATH}/x.png"))

        assert mock_request.call_args.kwargs["stream"] is True

    @patch("requests.request")
    def test_post_call_has_no_stream_kwarg(self, mock_request, gitlab_tool, mock_response):
        mock_request.return_value = mock_response

        gitlab_tool.execute({"method": "POST", "url": "/api/v4/projects", "method_arguments": {"name": "x"}})

        assert "stream" not in mock_request.call_args.kwargs

    @patch("requests.request")
    def test_content_type_with_params_and_upper_case_is_detected(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response(headers={"Content-Type": "image/PNG; charset=binary"})

        result = gitlab_tool.execute(_get("/api/v4/projects/1/repository/files/a/raw"))

        assert isinstance(result, GitlabMultimodalResponse)
        assert result.image_artifacts[0]["mime_type"] == "image/png"

    @patch("requests.request")
    def test_json_body_returns_plain_string(self, mock_request, gitlab_tool, mock_response):
        mock_response.headers = {"Content-Type": "application/json"}
        mock_request.return_value = mock_response

        result = gitlab_tool.execute(_get("/api/v4/projects"))

        assert not isinstance(result, GitlabMultimodalResponse)
        assert str(result) == 'HTTP: GET https://gitlab.example.com/api/v4/projects -> 200 OK {"id": 1, "name": "test"}'

    @patch("requests.request")
    def test_octet_stream_upload_detected_from_extension(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response(WEBP_BYTES, headers={"Content-Type": "application/octet-stream"})

        result = gitlab_tool.execute(_get(f"{UPLOAD_PATH}/x.webp"))

        assert isinstance(result, GitlabMultimodalResponse)
        assert result.image_artifacts[0]["mime_type"] == "image/webp"

    @patch("requests.request")
    def test_missing_content_type_upload_detected_from_extension(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response(JPEG_BYTES, headers={})

        result = gitlab_tool.execute(_get(f"{UPLOAD_PATH}/x.JPG"))

        assert isinstance(result, GitlabMultimodalResponse)
        assert result.image_artifacts[0]["mime_type"] == "image/jpeg"

    @patch("requests.request")
    def test_non_str_content_type_counts_as_missing(self, mock_request, gitlab_tool, mock_response):
        mock_request.return_value = mock_response  # Mock headers: Content-Type is a Mock, not a str

        result = gitlab_tool.execute(_get("/api/v4/projects"))

        assert not isinstance(result, GitlabMultimodalResponse)

    @patch("requests.request")
    def test_json_content_type_on_upload_path_is_not_an_image(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response(headers={"Content-Type": "application/json"})

        result = gitlab_tool.execute(_get(f"{UPLOAD_PATH}/x.png"))

        assert not isinstance(result, GitlabMultimodalResponse)

    @patch("requests.request")
    def test_svg_upload_is_not_an_image(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response(headers={"Content-Type": "application/octet-stream"})

        result = gitlab_tool.execute(_get(f"{UPLOAD_PATH}/x.svg"))

        assert not isinstance(result, GitlabMultimodalResponse)

    @patch("requests.request")
    def test_oversize_content_length_skips_without_reading(self, mock_request, gitlab_tool):
        response = _image_response(headers={"Content-Type": "image/png", "Content-Length": str(5 * 1024 * 1024 + 1)})
        mock_request.return_value = response

        result = gitlab_tool.execute(_get(f"{UPLOAD_PATH}/x.png"))
        text, artifacts = gitlab_tool._post_process_output_content(result)

        response.iter_content.assert_not_called()
        response.close.assert_called_once()
        assert "Image exceeds 5 MB limit; skipped." in text
        assert "-> 200 OK" in text
        assert artifacts is None

    @patch("requests.request")
    def test_oversize_stream_without_content_length_aborts(self, mock_request, gitlab_tool):
        chunk = b"\x00" * 65536
        chunks_read = []

        def endless(chunk_size):
            while True:
                chunks_read.append(1)
                yield chunk

        response = _image_response(headers={"Content-Type": "image/png"})
        response.iter_content = Mock(side_effect=endless)
        mock_request.return_value = response

        result = gitlab_tool.execute(_get(f"{UPLOAD_PATH}/x.png"))
        text, artifacts = gitlab_tool._post_process_output_content(result)

        assert "Image exceeds 5 MB limit; skipped." in text
        assert artifacts is None
        assert len(chunks_read) == 5 * 16 + 1
        response.close.assert_called_once()

    @patch("requests.request")
    def test_stream_error_mid_read_still_closes_response(self, mock_request, gitlab_tool):
        def broken(chunk_size):
            yield PNG_BYTES
            raise requests.exceptions.ChunkedEncodingError("connection broken")

        response = _image_response()
        response.iter_content = Mock(side_effect=broken)
        mock_request.return_value = response

        with pytest.raises(requests.exceptions.ChunkedEncodingError):
            gitlab_tool.execute(_get(f"{UPLOAD_PATH}/x.png"))

        response.close.assert_called_once()

    @patch("requests.request")
    def test_text_plain_upload_detected_from_extension(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response(headers={"Content-Type": "text/plain; charset=utf-8"})

        result = gitlab_tool.execute(_get(f"{UPLOAD_PATH}/x.png"))

        assert isinstance(result, GitlabMultimodalResponse)
        assert result.image_artifacts[0]["mime_type"] == "image/png"

    @patch("requests.request")
    def test_octet_stream_png_outside_uploads_is_not_an_image(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response(headers={"Content-Type": "application/octet-stream"})
        url = "/api/v4/projects/1/jobs/2/artifacts/out/screenshot.png"

        result = gitlab_tool.execute(_get(url))

        assert not isinstance(result, GitlabMultimodalResponse)
        assert str(result).startswith(f"HTTP: GET {BASE}{url} -> 200 OK")

    @patch("requests.request")
    def test_empty_image_body_is_skipped(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response(b"")

        text, artifacts = gitlab_tool._run(query=_get(f"{UPLOAD_PATH}/x.png"))

        assert "-> 200 OK" in text
        assert "Empty image body; skipped." in text
        assert artifacts is None

    @pytest.mark.parametrize(
        ("path", "headers"),
        [
            (f"{UPLOAD_PATH}/x.png", {"Content-Type": "application/octet-stream"}),
            (f"{UPLOAD_PATH}/x.jpg", {"Content-Type": "image/jpeg"}),
        ],
    )
    @patch("requests.request")
    def test_body_not_matching_image_signature_is_skipped(self, mock_request, gitlab_tool, path, headers):
        mock_request.return_value = _image_response(
            b"<html>not an image</html>" if "png" in path else PNG_BYTES, headers
        )

        text, artifacts = gitlab_tool._run(query=_get(path))

        assert "is not a valid image/" in text
        assert "skipped." in text
        assert artifacts is None

    @pytest.mark.parametrize(
        ("body", "path", "mime"),
        [
            (b"GIF89a" + b"\x00" * 8, f"{UPLOAD_PATH}/x.gif", "image/gif"),
            (b"GIF87a" + b"\x00" * 8, f"{UPLOAD_PATH}/x.gif", "image/gif"),
            (JPEG_BYTES, f"{UPLOAD_PATH}/x.jpeg", "image/jpeg"),
            (WEBP_BYTES, f"{UPLOAD_PATH}/x.webp", "image/webp"),
        ],
    )
    @patch("requests.request")
    def test_valid_signatures_are_attached(self, mock_request, gitlab_tool, body, path, mime):
        mock_request.return_value = _image_response(body, headers={"Content-Type": "application/octet-stream"})

        result = gitlab_tool.execute(_get(path))

        assert isinstance(result, GitlabMultimodalResponse)
        assert result.image_artifacts[0]["mime_type"] == mime

    @patch("requests.request")
    def test_not_found_on_image_url_returns_existing_error_string(self, mock_request, gitlab_tool):
        mock_request.return_value = _image_response(headers={"Content-Type": "image/png"}, status_code=404)

        text, artifacts = gitlab_tool._run(query=_get(f"{UPLOAD_PATH}/x.png"))

        assert text == (
            "HTTP: GET https://gitlab.example.com/group/proj/uploads/abc/x.png -> 404 Not Found "
            '{"message": "404 Not Found"}'
        )
        assert artifacts is None


class TestDescription:
    def test_description_documents_image_responses(self):
        description = GITLAB_TOOL.description

        assert "IMAGE RESPONSES" in description
        assert "IMAGE RESPONSES" in GITLAB_TOOL.user_description
        assert '{"query": {"method": "GET", "url": "/api/v4/projects/:id/uploads/:secret/:filename"}}' in description
        assert "/<namespace>/<project>/uploads/<secret>/<filename>" in description
        assert "/-/project/<id>/uploads/<secret>/<filename>" in description
        assert "Do not request the web upload link itself" in description
        assert "5 MB" in description
        assert "same origin" in description

    def test_user_description_has_upload_example_and_origin_note(self):
        user_description = GITLAB_TOOL.user_description

        assert '{"method": "GET", "url": "/api/v4/projects/:id/uploads/:secret/:filename"}' in user_description
        assert "web upload links" in user_description
        assert "same origin" in user_description
