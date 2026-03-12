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

from unittest.mock import patch

import pytest

from codemie_tools.core.vcs.utils import (
    _merge_custom_headers,
    _validate_custom_headers,
    _build_headers,
    file_response_handler,
    PROTECTED_HEADERS,
)


class TestVcsUtils:
    """Tests for VCS utility functions."""

    def test_merge_custom_headers_empty(self):
        """Test merging empty custom headers."""
        result = _merge_custom_headers({})
        assert result == {}

    def test_merge_custom_headers_none(self):
        """Test merging None custom headers."""
        result = _merge_custom_headers(None)
        assert result == {}

    def test_merge_custom_headers_valid(self):
        """Test merging valid custom headers."""
        custom_headers = {"X-Custom-Header": "value", "Content-Type": "application/json"}
        result = _merge_custom_headers(custom_headers)
        assert result == custom_headers

    def test_merge_custom_headers_protected(self):
        """Test that protected headers are ignored when merging."""
        custom_headers = {
            "X-Custom-Header": "value",
            "Authorization": "Bearer fake_token",
            "authorization": "Bearer another_fake_token",
        }
        result = _merge_custom_headers(custom_headers)
        assert "X-Custom-Header" in result
        assert "Authorization" not in result
        assert "authorization" not in result

    def test_validate_custom_headers_empty(self):
        """Test validating empty custom headers."""
        _validate_custom_headers({})  # Should not raise

    def test_validate_custom_headers_none(self):
        """Test validating None custom headers."""
        _validate_custom_headers(None)  # Should not raise

    def test_validate_custom_headers_valid(self):
        """Test validating valid custom headers."""
        custom_headers = {"X-Custom-Header": "value", "Content-Type": "application/json"}
        _validate_custom_headers(custom_headers)  # Should not raise

    def test_validate_custom_headers_protected(self):
        """Test that protected headers raise ValueError."""
        for protected_header in PROTECTED_HEADERS:
            custom_headers = {protected_header: "value"}
            with pytest.raises(ValueError) as excinfo:
                _validate_custom_headers(custom_headers)
            assert f"Cannot override protected header: {protected_header}" in str(excinfo.value)

    def test_build_headers_default(self):
        """Test building headers with default values."""
        default_headers = {"Accept": "application/json"}
        access_token = "test_token"

        result = _build_headers(default_headers, access_token)

        assert result["Accept"] == "application/json"
        assert result["Authorization"] == "Bearer test_token"

    def test_build_headers_with_custom(self):
        """Test building headers with custom values."""
        default_headers = {"Accept": "application/json"}
        access_token = "test_token"
        custom_headers = {"X-Custom-Header": "value", "Content-Type": "application/xml"}

        result = _build_headers(default_headers, access_token, custom_headers)

        assert result["Accept"] == "application/json"
        assert result["Authorization"] == "Bearer test_token"
        assert result["X-Custom-Header"] == "value"
        assert result["Content-Type"] == "application/xml"

    def test_build_headers_with_protected_attempt(self):
        """Test building headers with attempt to override protected headers."""
        default_headers = {"Accept": "application/json"}
        access_token = "test_token"
        custom_headers = {"Authorization": "Bearer fake_token"}

        result = _build_headers(default_headers, access_token, custom_headers)

        assert result["Accept"] == "application/json"
        assert result["Authorization"] == "Bearer test_token"  # Should not be overridden

    @patch("codemie_tools.core.vcs.utils.logger")
    def test_file_response_handler_non_file(self, mock_logger):
        """Test file_response_handler with non-file response."""

        @file_response_handler
        def mock_execute(*args, **kwargs):
            return "Not a file response"

        result = mock_execute(None)
        assert result == "Not a file response"

    @patch("codemie_tools.core.vcs.utils.logger")
    def test_file_response_handler_file_not_base64(self, mock_logger):
        """Test file_response_handler with file response not in base64."""

        @file_response_handler
        def mock_execute(*args, **kwargs):
            return {"type": "file", "encoding": "plain", "content": "test content"}

        mock_tool = type('MockTool', (), {'tokens_size_limit': 1000})()
        result = mock_execute(mock_tool)

        assert result["type"] == "file"
        assert result["encoding"] == "plain"
        assert result["content"] == "test content"
        assert "error" not in result

    @patch("codemie_tools.core.vcs.utils.logger")
    def test_file_response_handler_file_base64_success(self, mock_logger):
        """Test file_response_handler with valid base64 file response."""
        # "test content" in base64
        base64_content = "dGVzdCBjb250ZW50"

        @file_response_handler
        def mock_execute(*args, **kwargs):
            return {"type": "file", "encoding": "base64", "content": base64_content, "size": 100}

        mock_tool = type('MockTool', (), {'tokens_size_limit': 1000})()
        result = mock_execute(mock_tool)

        assert result["type"] == "file"
        assert result["encoding"] == "base64"
        assert result["content"] == "test content"  # Should be decoded
        assert "error" not in result

    @patch("codemie_tools.core.vcs.utils.logger")
    def test_file_response_handler_file_too_large(self, mock_logger):
        """Test file_response_handler with file too large for decoding."""

        @file_response_handler
        def mock_execute(*args, **kwargs):
            return {
                "type": "file",
                "encoding": "base64",
                "content": "dGVzdCBjb250ZW50",
                "size": 20000,  # Large size that exceeds the limit
            }

        mock_tool = type('MockTool', (), {'tokens_size_limit': 1000})()
        result = mock_execute(mock_tool)

        assert result["type"] == "file"
        assert result["encoding"] == "base64"
        assert "error" in result
        assert "File too large for Base64 decoding" in result["error"]

    @patch("codemie_tools.core.vcs.utils.logger")
    def test_file_response_handler_invalid_base64(self, mock_logger):
        """Test file_response_handler with invalid base64 content."""

        @file_response_handler
        def mock_execute(*args, **kwargs):
            return {"type": "file", "encoding": "base64", "content": "invalid base64!", "size": 100}

        mock_tool = type('MockTool', (), {'tokens_size_limit': 1000})()
        result = mock_execute(mock_tool)

        assert result["type"] == "file"
        assert result["encoding"] == "base64"
        assert "error" in result
