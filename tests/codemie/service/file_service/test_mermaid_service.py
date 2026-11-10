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

import base64

import pytest

import requests

from unittest.mock import Mock, patch
from codemie.configs.config import Config
from codemie.core.constants import MermaidContentType, MermaidMimeType
from codemie.service.file_service.mermaid_service import (
    MermaidDiagramGenerationError,
    MermaidNotConfiguredError,
    MermaidService,
    MermaidServiceUnavailableError,
)


class TestMermaidService:
    @patch("codemie.service.file_service.mermaid_service.config")
    @patch.object(MermaidService, "_make_internal_request")
    def test_draw_mermaid_svg_success(self, mock_internal, mock_config):
        mock_config.MERMAID_USE_MERMAID_INC = False
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": MermaidMimeType.SVG}
        mock_response.content = b"<svg>diagram</svg>"
        mock_internal.return_value = mock_response

        result = MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)
        assert result == b"<svg>diagram</svg>"
        mock_internal.assert_called_once()

    @patch("codemie.service.file_service.mermaid_service.config")
    @patch.object(MermaidService, "_make_internal_request")
    def test_draw_mermaid_png_success(self, mock_internal, mock_config):
        mock_config.MERMAID_USE_MERMAID_INC = False
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": MermaidMimeType.PNG}
        mock_response.content = b"\x89PNG\r\n"
        mock_internal.return_value = mock_response

        result = MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.PNG)
        assert result == b"\x89PNG\r\n"
        mock_internal.assert_called_once()

    @patch("codemie.service.file_service.mermaid_service.config")
    @patch.object(MermaidService, "_make_internal_request")
    def test_draw_mermaid_non_200_status(self, mock_internal, mock_config):
        mock_config.MERMAID_USE_MERMAID_INC = False
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.headers = {"Content-Type": MermaidMimeType.SVG}
        mock_response.content = b""
        mock_internal.return_value = mock_response

        with pytest.raises(MermaidDiagramGenerationError):
            MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)

    @patch("codemie.service.file_service.mermaid_service.config")
    @patch.object(MermaidService, "_make_internal_request")
    def test_draw_mermaid_unexpected_content_type(self, mock_internal, mock_config):
        mock_config.MERMAID_USE_MERMAID_INC = False
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.content = b"<html></html>"
        mock_internal.return_value = mock_response

        with pytest.raises(MermaidServiceUnavailableError):
            MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)

    @patch("codemie.service.file_service.mermaid_service.config")
    @patch.object(MermaidService, "_make_internal_request")
    def test_draw_mermaid_exception(self, mock_internal, mock_config):
        mock_config.MERMAID_USE_MERMAID_INC = False
        mock_internal.side_effect = requests.exceptions.ConnectionError("Network error")
        with pytest.raises(MermaidServiceUnavailableError):
            MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)


class TestMermaidServiceExternal:
    @patch("codemie.service.file_service.mermaid_service.requests.get")
    @patch("codemie.service.file_service.mermaid_service.config")
    def test_draw_mermaid_external_success(self, mock_config, mock_get):
        mock_config.MERMAID_USE_MERMAID_INC = True
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": MermaidMimeType.SVG}
        mock_response.content = b"<svg>external</svg>"
        mock_get.return_value = mock_response

        result = MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)
        assert result == b"<svg>external</svg>"
        mock_get.assert_called_once()
        encoded = base64.b64encode("graph TD; A-->B;".encode("utf8")).decode("ascii")
        assert encoded in mock_get.call_args[0][0]

    @patch("codemie.service.file_service.mermaid_service.requests.get")
    @patch("codemie.service.file_service.mermaid_service.config")
    def test_draw_mermaid_external_non_200_status(self, mock_config, mock_get):
        mock_config.MERMAID_USE_MERMAID_INC = True
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.headers = {"Content-Type": MermaidMimeType.SVG}
        mock_response.content = b""
        mock_get.return_value = mock_response

        with pytest.raises(MermaidDiagramGenerationError):
            MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)

    @patch("codemie.service.file_service.mermaid_service.requests.get")
    @patch("codemie.service.file_service.mermaid_service.config")
    def test_draw_mermaid_external_unexpected_content_type(self, mock_config, mock_get):
        mock_config.MERMAID_USE_MERMAID_INC = True
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.content = b"<html></html>"
        mock_get.return_value = mock_response

        with pytest.raises(MermaidServiceUnavailableError):
            MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)

    @patch("codemie.service.file_service.mermaid_service.requests.get")
    @patch("codemie.service.file_service.mermaid_service.config")
    def test_draw_mermaid_external_exception(self, mock_config, mock_get):
        mock_config.MERMAID_USE_MERMAID_INC = True
        mock_get.side_effect = requests.exceptions.ConnectionError("Network error")
        with pytest.raises(MermaidServiceUnavailableError):
            MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)


class TestMermaidServiceConditional:
    @patch.object(MermaidService, "_make_external_request")
    @patch("codemie.service.file_service.mermaid_service.config")
    def test_draw_mermaid_calls_external_when_flag_true(self, mock_config, mock_external):
        mock_config.MERMAID_USE_MERMAID_INC = True
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": MermaidMimeType.SVG}
        mock_response.content = b"external"
        mock_external.return_value = mock_response
        result = MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)
        assert result == b"external"
        mock_external.assert_called_once()

    @patch.object(MermaidService, "_make_internal_request")
    @patch("codemie.service.file_service.mermaid_service.config")
    def test_draw_mermaid_calls_internal_when_flag_false(self, mock_config, mock_internal):
        mock_config.MERMAID_USE_MERMAID_INC = False
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": MermaidMimeType.SVG}
        mock_response.content = b"internal"
        mock_internal.return_value = mock_response
        result = MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)
        assert result == b"internal"
        mock_internal.assert_called_once()


def _response(status_code, content_type=MermaidMimeType.SVG):
    response = Mock()
    response.status_code = status_code
    response.headers = {"Content-Type": content_type}
    response.content = b"<svg/>"
    return response


class TestMermaidServiceIsConfigured:
    @patch("codemie.service.file_service.mermaid_service.config")
    def test_is_configured_true_when_server_url_set(self, mock_config):
        mock_config.MERMAID_SERVER_URL = "http://localhost:8082"
        mock_config.MERMAID_USE_MERMAID_INC = False
        assert MermaidService.is_configured() is True

    @patch("codemie.service.file_service.mermaid_service.config")
    def test_is_configured_true_when_mermaid_inc_enabled(self, mock_config):
        mock_config.MERMAID_SERVER_URL = ""
        mock_config.MERMAID_USE_MERMAID_INC = True
        assert MermaidService.is_configured() is True

    @patch("codemie.service.file_service.mermaid_service.config")
    def test_is_configured_false_when_neither_set(self, mock_config):
        mock_config.MERMAID_SERVER_URL = ""
        mock_config.MERMAID_USE_MERMAID_INC = False
        assert MermaidService.is_configured() is False

    def test_mermaid_enabled_defaults_true(self):
        assert Config.model_fields["MERMAID_ENABLED"].default is True


@patch("codemie.service.file_service.mermaid_service.config")
@patch.object(MermaidService, "_make_internal_request")
class TestMermaidServiceErrorClassification:
    def _configure(self, mock_config):
        mock_config.MERMAID_SERVER_URL = "http://localhost:8082"
        mock_config.MERMAID_USE_MERMAID_INC = False

    def test_draw_mermaid_raises_not_configured_without_request(self, mock_internal, mock_config):
        mock_config.MERMAID_SERVER_URL = ""
        mock_config.MERMAID_USE_MERMAID_INC = False
        with pytest.raises(MermaidNotConfiguredError):
            MermaidService.draw_mermaid("graph TD; A-->B;")
        mock_internal.assert_not_called()

    def test_draw_mermaid_connection_error_raises_service_unavailable(self, mock_internal, mock_config):
        self._configure(mock_config)
        mock_internal.side_effect = requests.exceptions.ConnectionError("refused")
        with pytest.raises(MermaidServiceUnavailableError):
            MermaidService.draw_mermaid("graph TD; A-->B;")

    @pytest.mark.parametrize("status_code", [401, 403, 404, 408, 413, 429, 500, 502, 503])
    def test_draw_mermaid_renderer_fault_raises_service_unavailable(self, mock_internal, mock_config, status_code):
        self._configure(mock_config)
        mock_internal.return_value = _response(status_code)
        with pytest.raises(MermaidServiceUnavailableError):
            MermaidService.draw_mermaid("graph TD; A-->B;")

    @pytest.mark.parametrize("status_code", [400, 422])
    def test_draw_mermaid_syntax_rejection_raises_generation_error(self, mock_internal, mock_config, status_code):
        self._configure(mock_config)
        mock_internal.return_value = _response(status_code)
        with pytest.raises(MermaidDiagramGenerationError) as exc_info:
            MermaidService.draw_mermaid("graph TD; A-->B;")
        assert not isinstance(exc_info.value, MermaidServiceUnavailableError)

    def test_draw_mermaid_bad_content_type_raises_service_unavailable(self, mock_internal, mock_config):
        self._configure(mock_config)
        mock_internal.return_value = _response(200, "text/html")
        with pytest.raises(MermaidServiceUnavailableError):
            MermaidService.draw_mermaid("graph TD; A-->B;")

    def test_draw_mermaid_unavailable_message_hides_renderer_url(self, mock_internal, mock_config):
        self._configure(mock_config)
        mock_internal.side_effect = requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='mermaid-internal', port=8082): Max retries exceeded with url: /generate"
        )
        with pytest.raises(MermaidServiceUnavailableError) as exc_info:
            MermaidService.draw_mermaid("graph TD; A-->B;")
        assert "mermaid-internal" not in str(exc_info.value)
        assert "8082" not in str(exc_info.value)

    def test_draw_mermaid_unexpected_exception_propagates(self, mock_internal, mock_config):
        self._configure(mock_config)
        mock_internal.side_effect = KeyError("x")
        with pytest.raises(KeyError):
            MermaidService.draw_mermaid("graph TD; A-->B;")


@patch("codemie.service.file_service.mermaid_service.requests.get")
@patch("codemie.service.file_service.mermaid_service.config")
def test_external_request_passes_timeout(mock_config, mock_get):
    mock_config.MERMAID_SERVER_TIMEOUT = 17
    MermaidService._make_external_request("graph TD; A-->B;", MermaidContentType.SVG)
    assert mock_get.call_args.kwargs["timeout"] == 17
