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
Core integration test configuration for enterprise features.

Tests in this directory verify:
- Loader availability checks
- Enterprise dependencies initialization
- Graceful degradation when enterprise not installed
- Priority order (HAS_<SERVICE> > config)
"""

from __future__ import annotations

import pytest


@pytest.fixture
def mock_enterprise_installed(monkeypatch):
    """
    Fixture to simulate enterprise package installed.

    Usage:
        def test_with_enterprise(mock_enterprise_installed):
            from codemie.enterprise.loader import HAS_LANGFUSE
            assert HAS_LANGFUSE is True
    """
    # Import real classes if available
    try:
        from codemie_enterprise.langfuse import LangFuseConfig, LangFuseService
    except ImportError:
        # If not available, create mock classes
        LangFuseConfig = type('LangFuseConfig', (), {})  # noqa: N806
        LangFuseService = type('LangFuseService', (), {})  # noqa: N806

    monkeypatch.setattr("codemie.enterprise.loader.HAS_LANGFUSE", True)
    monkeypatch.setattr("codemie.enterprise.loader.LangFuseConfig", LangFuseConfig)
    monkeypatch.setattr("codemie.enterprise.loader.LangFuseService", LangFuseService)

    return True


@pytest.fixture
def mock_enterprise_not_installed(monkeypatch):
    """
    Fixture to simulate enterprise package NOT installed.

    Usage:
        def test_without_enterprise(mock_enterprise_not_installed):
            from codemie.enterprise.loader import HAS_LANGFUSE
            assert HAS_LANGFUSE is False
    """
    monkeypatch.setattr("codemie.enterprise.loader.HAS_LANGFUSE", False)
    monkeypatch.setattr("codemie.enterprise.loader.LangFuseConfig", None)
    monkeypatch.setattr("codemie.enterprise.loader.LangFuseService", None)
    monkeypatch.setattr("codemie.enterprise.loader.TraceContext", None)
    monkeypatch.setattr("codemie.enterprise.loader.SpanContext", None)
    monkeypatch.setattr("codemie.enterprise.loader.LangfuseContextManager", None)
    monkeypatch.setattr("codemie.enterprise.loader.build_workflow_metadata", None)
    monkeypatch.setattr("codemie.enterprise.loader.build_agent_metadata", None)

    # CRITICAL: Reset global service to None (prevents MagicMock leakage from other tests)
    from codemie.enterprise.langfuse import set_global_langfuse_service

    set_global_langfuse_service(None)

    return False
