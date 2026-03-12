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
Test suite for MCP execution context with request_headers.

Tests MCPExecutionContext model's ability to store and propagate request headers.
Note: Full integration testing with MCPToolkitService should be done in integration test suite
      to avoid circular import issues during testing.
"""


class TestMCPExecutionContextHeadersSupport:
    """Test cases for MCPExecutionContext request_headers field support."""

    def test_mcp_execution_context_creation_with_headers(self):
        """
        TC-1.3.3: Verify MCPExecutionContext can be created with request_headers.

        Priority: Critical

        Tests that execution context properly stores headers for propagation.
        """
        # Arrange
        from codemie.service.mcp.models import MCPExecutionContext

        test_headers = {'X-Tenant-ID': 'tenant-123', 'X-User-ID': 'user-456'}

        # Act
        context = MCPExecutionContext(
            user_id='user-123',
            assistant_id='asst-456',
            project_name='test-project',
            workflow_execution_id='wf-789',
            request_headers=test_headers,
        )

        # Assert - context created successfully with headers
        assert context.user_id == 'user-123'
        assert context.assistant_id == 'asst-456'
        assert context.request_headers == test_headers
        assert context.request_headers['X-Tenant-ID'] == 'tenant-123'

    def test_mcp_execution_context_creation_without_headers(self):
        """
        TC-1.3.4: Verify backward compatibility - MCPExecutionContext works without headers.

        Priority: High

        Tests that execution context works when request_headers is not provided.
        """
        # Arrange
        from codemie.service.mcp.models import MCPExecutionContext

        # Act - create without request_headers
        context = MCPExecutionContext(user_id='user-123', assistant_id='asst-456', project_name='test-project')

        # Assert - context created successfully
        assert context.user_id == 'user-123'
        assert context.assistant_id == 'asst-456'
        assert context.request_headers is None  # Default value
