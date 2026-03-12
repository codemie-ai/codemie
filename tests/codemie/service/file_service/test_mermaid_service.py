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

from unittest.mock import Mock, patch
from codemie.core.constants import MermaidContentType, MermaidMimeType
from codemie.service.file_service.mermaid_service import MermaidService


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
        mock_response.status_code = 500
        mock_response.headers = {"Content-Type": MermaidMimeType.SVG}
        mock_response.content = b""
        mock_internal.return_value = mock_response

        with pytest.raises(RuntimeError):
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

        with pytest.raises(ValueError):
            MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)

    @patch("codemie.service.file_service.mermaid_service.config")
    @patch.object(MermaidService, "_make_internal_request")
    def test_draw_mermaid_exception(self, mock_internal, mock_config):
        mock_config.MERMAID_USE_MERMAID_INC = False
        mock_internal.side_effect = Exception("Network error")
        with pytest.raises(RuntimeError):
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
        mock_response.status_code = 500
        mock_response.headers = {"Content-Type": MermaidMimeType.SVG}
        mock_response.content = b""
        mock_get.return_value = mock_response

        with pytest.raises(RuntimeError):
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

        with pytest.raises(ValueError):
            MermaidService.draw_mermaid("graph TD; A-->B;", type=MermaidContentType.SVG)

    @patch("codemie.service.file_service.mermaid_service.requests.get")
    @patch("codemie.service.file_service.mermaid_service.config")
    def test_draw_mermaid_external_exception(self, mock_config, mock_get):
        mock_config.MERMAID_USE_MERMAID_INC = True
        mock_get.side_effect = Exception("Network error")
        with pytest.raises(RuntimeError):
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
