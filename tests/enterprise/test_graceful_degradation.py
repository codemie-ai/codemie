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

"""Tests for graceful degradation when enterprise not available"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


def test_agent_utils_without_enterprise(mock_enterprise_not_installed):
    """Test agent building works without enterprise"""
    # This would test actual agent code that uses callbacks
    # Mocking to demonstrate pattern
    from codemie.enterprise.langfuse import get_langfuse_callback_handler

    handler = get_langfuse_callback_handler()
    assert handler is None

    # Agent should build with empty callbacks list
    callbacks = []
    if handler:
        callbacks.append(handler)

    # Should work fine with empty list
    assert callbacks == []


def test_monitoring_service_without_enterprise(mock_enterprise_not_installed):
    """Test monitoring service degrades gracefully"""
    from codemie.enterprise.langfuse import (
        get_langfuse_client_or_none,
        set_global_langfuse_service,
    )

    # Ensure global service is None
    set_global_langfuse_service(None)

    client = get_langfuse_client_or_none()
    assert client is None

    # Monitoring should skip tracing
    if client:
        client.track_usage(model="test", tokens=100)
    # No error should be raised


def test_evaluation_service_requires_enterprise(mock_enterprise_not_installed):
    """Test evaluation service returns clear error"""
    from codemie.enterprise.langfuse import require_langfuse_client
    from codemie.core.exceptions import ExtendedHTTPException

    mock_request = MagicMock()
    mock_request.app.state.langfuse_service = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        require_langfuse_client(mock_request)

    assert exc_info.value.code == 503
    assert "LangFuse" in exc_info.value.message
