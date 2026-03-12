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
from langchain_core.tools import ToolException

from codemie_tools.core.project_management.confluence.utils import (
    validate_creds,
    prepare_page_payload,
    parse_payload_params,
)


class TestValidateConfluenceCreds:
    def test_valid_creds(self):
        confluence_mock = MagicMock()
        confluence_mock.url = "https://confluence.example.com"

        # This should not raise an exception
        validate_creds(confluence_mock)

    def test_empty_url(self):
        confluence_mock = MagicMock()
        confluence_mock.url = ""

        with pytest.raises(ToolException, match="Confluence URL is required"):
            validate_creds(confluence_mock)

    def test_none_url(self):
        confluence_mock = MagicMock()
        confluence_mock.url = None

        with pytest.raises(ToolException, match="Confluence URL is required"):
            validate_creds(confluence_mock)


class TestPreparePagePayload:
    def test_convert_markdown_to_html(self):
        payload = {
            "body": {
                "storage": {
                    "value": "# Heading\n\nThis is a paragraph with **bold** text.",
                    "representation": "storage",
                }
            }
        }

        result = prepare_page_payload(payload)

        # Check that markdown was converted to HTML
        assert "<h1>Heading</h1>" in result["body"]["storage"]["value"]
        assert "<strong>bold</strong>" in result["body"]["storage"]["value"]

    def test_no_body_field(self):
        payload = {"title": "Test Page"}

        result = prepare_page_payload(payload)

        # Should return unchanged payload
        assert result == payload

    def test_empty_payload(self):
        payload = {}

        result = prepare_page_payload(payload)

        # Should return unchanged empty payload
        assert result == {}


class TestParsePayloadParams:
    def test_valid_json(self):
        params = '{"title": "Test Page", "space": {"key": "TEST"}}'

        result = parse_payload_params(params)

        assert result == {"title": "Test Page", "space": {"key": "TEST"}}

    def test_empty_string(self):
        params = ""

        result = parse_payload_params(params)

        assert result == {}

    def test_none_params(self):
        params = None

        result = parse_payload_params(params)

        assert result == {}

    def test_invalid_json(self):
        params = '{"title": "Test Page", "space": {"key": "TEST"'

        with pytest.raises(ToolException, match="Confluence tool exception"):
            parse_payload_params(params)

    @patch("codemie_tools.core.project_management.confluence.utils.clean_json_string")
    def test_clean_json_string_called(self, mock_clean_json):
        mock_clean_json.return_value = '{"title": "Test"}'
        params = '{"title": "Test"}'

        parse_payload_params(params)

        mock_clean_json.assert_called_once_with(params)
