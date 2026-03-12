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

"""
Test suite for request utilities including custom header extraction.

Tests the extract_custom_headers function for MCP header propagation including
filtering, security blocking, and edge cases.
"""

from unittest.mock import Mock, patch


from codemie.rest_api.utils.request_utils import extract_custom_headers


class TestExtractCustomHeaders:
    """Test cases for extract_custom_headers function."""

    def test_extract_x_headers(self):
        """
        TC-1.4.1: Verify extraction of X-prefixed headers.

        Priority: Critical
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {
            'X-Tenant-ID': 'abc',
            'X-User': 'john',
            'Content-Type': 'application/json',
            'Authorization': 'Bearer token',
        }

        # Act
        result = extract_custom_headers(mock_request, propagate=True)

        # Assert - only X- headers extracted
        assert result is not None
        assert 'X-Tenant-ID' in result
        assert 'X-User' in result
        assert result['X-Tenant-ID'] == 'abc'
        assert result['X-User'] == 'john'

        # Assert - non-X headers excluded
        assert 'Content-Type' not in result
        assert 'Authorization' not in result

    def test_extract_blocked_headers_filtered(self):
        """
        TC-1.4.2: Verify blocked headers are filtered out.

        Priority: Critical
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {
            'X-Tenant-ID': 'abc',
            'X-Auth-Token': 'secret',  # Should be blocked
            'Authorization': 'Bearer token',  # Not X- but should not appear anyway
            'X-Api-Key': 'key123',  # Should be blocked
        }

        # Act
        with patch('codemie.rest_api.utils.request_utils.config') as mock_config:
            mock_config.MCP_BLOCKED_HEADERS = 'authorization,cookie,x-api-key,x-auth-token,x-internal-secret'
            result = extract_custom_headers(mock_request, propagate=True)

        # Assert - only non-blocked X- headers returned
        assert result is not None
        assert 'X-Tenant-ID' in result
        assert result['X-Tenant-ID'] == 'abc'

        # Assert - blocked headers removed
        assert 'X-Auth-Token' not in result
        assert 'X-Api-Key' not in result
        assert 'Authorization' not in result

    def test_extract_with_propagate_false(self):
        """
        TC-1.4.3: Verify no headers extracted when propagation disabled.

        Priority: Critical
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {'X-Tenant-ID': 'abc', 'X-User': 'john'}

        # Act
        result = extract_custom_headers(mock_request, propagate=False)

        # Assert - None returned when propagation disabled
        assert result is None

    def test_extract_no_x_headers(self):
        """
        TC-1.4.4: Verify behavior when no custom headers present.

        Priority: High
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer token',
            'Accept': 'application/json',
        }

        # Act
        result = extract_custom_headers(mock_request, propagate=True)

        # Assert - None returned when no X- headers
        assert result is None

    def test_extract_case_insensitive_matching(self):
        """
        TC-1.4.5: Verify case-insensitive header matching.

        Priority: High
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {
            'x-tenant-id': 'abc',  # lowercase
            'X-USER-ID': 'john',  # uppercase
            'X-Session-Id': 'xyz',  # mixed case
        }

        # Act
        with patch('codemie.rest_api.utils.request_utils.config') as mock_config:
            mock_config.MCP_BLOCKED_HEADERS = 'authorization,cookie'
            result = extract_custom_headers(mock_request, propagate=True)

        # Assert - all X- headers extracted regardless of case
        assert result is not None
        assert len(result) == 3
        assert 'x-tenant-id' in result
        assert 'X-USER-ID' in result
        assert 'X-Session-Id' in result

    def test_extract_case_insensitive_blocking(self):
        """
        TC-1.4.6: Verify blocked headers matched case-insensitively.

        Priority: High
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {
            'X-Auth-Token': 'secret',  # Mixed case
            'x-auth-token': 'secret2',  # Lowercase
            'X-API-KEY': 'key',  # Uppercase
            'X-Tenant-ID': 'tenant',  # Should NOT be blocked
        }

        # Act
        with patch('codemie.rest_api.utils.request_utils.config') as mock_config:
            mock_config.MCP_BLOCKED_HEADERS = 'x-auth-token,x-api-key'
            result = extract_custom_headers(mock_request, propagate=True)

        # Assert - all variations of blocked headers removed
        assert result is not None
        assert 'X-Tenant-ID' in result  # Not blocked

        # Assert - all case variations of blocked headers removed
        assert 'X-Auth-Token' not in result
        assert 'x-auth-token' not in result
        assert 'X-API-KEY' not in result

    def test_extract_all_headers_blocked_returns_none(self):
        """
        Verify None returned when all X- headers are blocked.

        Priority: High
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {'X-Auth-Token': 'secret', 'X-Api-Key': 'key123'}

        # Act
        with patch('codemie.rest_api.utils.request_utils.config') as mock_config:
            mock_config.MCP_BLOCKED_HEADERS = 'x-auth-token,x-api-key'
            result = extract_custom_headers(mock_request, propagate=True)

        # Assert - None when all headers blocked
        assert result is None

    def test_extract_empty_request_headers(self):
        """
        Verify handling of request with no headers.

        Priority: High
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {}

        # Act
        result = extract_custom_headers(mock_request, propagate=True)

        # Assert - None for empty headers
        assert result is None

    def test_extract_with_special_characters(self):
        """
        TC-1.4.8: Verify handling of headers with special characters.

        Priority: Medium (included for completeness)
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {
            'X-Tenant-ID': 'abc-123_xyz',
            'X-Data': 'value=test&foo=bar',
            'X-Token': 'Bearer abc123-def456_ghi789',
        }

        # Act
        with patch('codemie.rest_api.utils.request_utils.config') as mock_config:
            mock_config.MCP_BLOCKED_HEADERS = 'authorization,cookie'
            result = extract_custom_headers(mock_request, propagate=True)

        # Assert - special characters preserved
        assert result is not None
        assert result['X-Tenant-ID'] == 'abc-123_xyz'
        assert result['X-Data'] == 'value=test&foo=bar'
        assert result['X-Token'] == 'Bearer abc123-def456_ghi789'

    def test_extract_with_empty_string_in_blocked_list(self):
        """
        TC-1.4.7: Verify handling of malformed blocked headers config.

        Priority: Medium (included for completeness)
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {'X-Tenant-ID': 'abc', 'X-User-ID': 'john'}

        # Act
        with patch('codemie.rest_api.utils.request_utils.config') as mock_config:
            mock_config.MCP_BLOCKED_HEADERS = 'authorization,,cookie, ,x-api-key'
            result = extract_custom_headers(mock_request, propagate=True)

        # Assert - empty strings don't cause issues
        assert result is not None
        assert 'X-Tenant-ID' in result
        assert 'X-User-ID' in result

    def test_extract_preserves_header_case(self):
        """
        Verify original header case is preserved in output.

        Priority: High
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {'X-Tenant-ID': 'abc', 'x-user-id': 'john', 'X-SESSION-ID': 'xyz'}

        # Act
        with patch('codemie.rest_api.utils.request_utils.config') as mock_config:
            mock_config.MCP_BLOCKED_HEADERS = 'authorization,cookie'
            result = extract_custom_headers(mock_request, propagate=True)

        # Assert - original case preserved
        assert 'X-Tenant-ID' in result  # Original case
        assert 'x-user-id' in result  # Original case
        assert 'X-SESSION-ID' in result  # Original case

    @patch('codemie.rest_api.utils.request_utils.logger')
    def test_extract_logging(self, mock_logger):
        """
        TC-1.4.9: Verify debug logging works correctly.

        Priority: Low (included for completeness)
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {'X-Tenant-ID': 'abc', 'X-User-ID': 'john'}

        # Act
        with patch('codemie.rest_api.utils.request_utils.config') as mock_config:
            mock_config.MCP_BLOCKED_HEADERS = 'authorization,cookie'
            extract_custom_headers(mock_request, propagate=True)

        # Assert - logger.debug called
        mock_logger.debug.assert_called_once()
        call_args = mock_logger.debug.call_args

        # Verify log message contains header count
        assert 'Extracted' in call_args[0][0]
        assert 'custom headers' in call_args[0][0]

        # Verify extra context
        assert 'extra' in call_args[1]
        assert call_args[1]['extra']['propagate_headers'] is True
        assert call_args[1]['extra']['header_count'] == 2

    def test_extract_with_unicode_values(self):
        """
        Verify Unicode characters in header values are preserved.

        Priority: High
        """
        # Arrange
        mock_request = Mock()
        mock_request.headers = {'X-User-Name': 'José García', 'X-Location': 'São Paulo'}

        # Act
        with patch('codemie.rest_api.utils.request_utils.config') as mock_config:
            mock_config.MCP_BLOCKED_HEADERS = 'authorization,cookie'
            result = extract_custom_headers(mock_request, propagate=True)

        # Assert - Unicode preserved
        assert result is not None
        assert result['X-User-Name'] == 'José García'
        assert result['X-Location'] == 'São Paulo'
