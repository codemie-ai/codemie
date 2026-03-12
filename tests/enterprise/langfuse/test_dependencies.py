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

"""Tests for enterprise_dependencies.py initialization helpers"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_initialize_langfuse_when_not_available(mock_enterprise_not_installed):
    """
    CRITICAL TEST: Verify HAS_LANGFUSE takes precedence over config.

    When HAS_LANGFUSE = False, service should NOT initialize even if
    config.LANGFUSE_TRACES = True.
    """
    from codemie.enterprise.langfuse import initialize_langfuse_from_config

    with patch("codemie.configs.config") as mock_config:
        mock_config.LANGFUSE_TRACES = True  # Config says ENABLE

        # HAS_LANGFUSE = False (package not installed)
        service = initialize_langfuse_from_config()

        # Expected: Service is None (priority: HAS_LANGFUSE > config)
        assert service is None


def test_initialize_langfuse_when_available_but_disabled(mock_enterprise_installed):
    """Test service not initialized when config disables it"""
    from codemie.enterprise.langfuse import initialize_langfuse_from_config

    with patch("codemie.configs.config") as mock_config:
        mock_config.LANGFUSE_TRACES = False  # Config says DISABLE

        service = initialize_langfuse_from_config()
        assert service is None


def test_initialize_langfuse_success(mock_enterprise_installed):
    """Test service initializes successfully when both checks pass"""
    from codemie.enterprise.langfuse import initialize_langfuse_from_config

    # Need to patch HAS_LANGFUSE in dependencies module (where it's imported from loader)
    with patch("codemie.enterprise.langfuse.dependencies.HAS_LANGFUSE", True):
        with patch("codemie.configs.config") as mock_config:
            with patch("codemie.configs.logger"):
                # Patch where classes are IMPORTED (codemie.enterprise)
                with patch("codemie.enterprise.LangFuseService") as mock_service_class:
                    with patch("codemie.enterprise.LangFuseConfig") as mock_config_class:
                        with patch("os.environ") as mock_environ:
                            # Setup mocks
                            mock_config.LANGFUSE_TRACES = True
                            mock_config.LANGFUSE_BLOCKED_INSTRUMENTATION_SCOPES = []
                            mock_config.ENV = "test"

                            mock_environ.get.side_effect = lambda key, default=None: {
                                "LANGFUSE_HOST": "http://localhost:3000",
                                "LANGFUSE_PUBLIC_KEY": "pk-test",
                                "LANGFUSE_SECRET_KEY": "sk-test",
                            }.get(key, default)

                            mock_service = MagicMock()
                            mock_service_class.return_value = mock_service

                            mock_langfuse_config = MagicMock()
                            mock_config_class.return_value = mock_langfuse_config

                            service = initialize_langfuse_from_config()

                            # Verify service was created and initialized
                            assert service is not None
                            mock_service.initialize.assert_called_once()


def test_get_langfuse_callback_handler_not_available(mock_enterprise_not_installed):
    """Test callback handler returns None when enterprise not available"""
    from codemie.enterprise.langfuse import get_langfuse_callback_handler

    handler = get_langfuse_callback_handler()
    assert handler is None


def test_get_langfuse_callback_handler_disabled_by_config(mock_enterprise_installed):
    """Test callback handler returns None when disabled by config"""
    from codemie.enterprise.langfuse import get_langfuse_callback_handler

    with patch("codemie.configs.config") as mock_config:
        mock_config.LANGFUSE_TRACES = False

        handler = get_langfuse_callback_handler()
        assert handler is None


def test_get_langfuse_callback_handler_success(mock_enterprise_installed):
    """Test callback handler returns handler when available"""
    from codemie.enterprise.langfuse import get_langfuse_callback_handler
    from codemie.configs.config import config

    # Need to patch HAS_LANGFUSE in dependencies module (where it's imported from loader)
    with patch("codemie.enterprise.langfuse.dependencies.HAS_LANGFUSE", True):
        with patch.object(config, 'LANGFUSE_TRACES', True):
            with patch("codemie.enterprise.langfuse.dependencies.get_global_langfuse_service") as mock_get_service:
                mock_service = MagicMock()
                mock_handler_instance = MagicMock()
                mock_service.get_callback_handler.return_value = mock_handler_instance
                mock_get_service.return_value = mock_service

                handler = get_langfuse_callback_handler()

                assert handler is not None
                assert handler is mock_handler_instance
                mock_service.get_callback_handler.assert_called_once()


def test_get_langfuse_callback_handler_service_not_initialized(mock_enterprise_installed):
    """Test callback handler returns None when service not initialized"""
    from codemie.enterprise.langfuse import get_langfuse_callback_handler
    from codemie.configs.config import config

    with patch("codemie.enterprise.langfuse.dependencies.HAS_LANGFUSE", True):
        with patch.object(config, 'LANGFUSE_TRACES', True):
            with patch("codemie.enterprise.langfuse.dependencies.get_global_langfuse_service") as mock_get_service:
                # Service returns None (not initialized)
                mock_get_service.return_value = None

                handler = get_langfuse_callback_handler()
                assert handler is None


def test_global_service_registry():
    """Test global service get/set functions"""
    from codemie.enterprise.langfuse import (
        set_global_langfuse_service,
        get_global_langfuse_service,
    )

    mock_service = MagicMock()
    set_global_langfuse_service(mock_service)

    retrieved = get_global_langfuse_service()
    assert retrieved is mock_service


def test_priority_order_documented():
    """
    Documentation test: Verify priority order is clearly documented.

    This test serves as living documentation of the critical priority rule:
    HAS_LANGFUSE > config.LANGFUSE_TRACES
    """
    from codemie.enterprise.langfuse import is_langfuse_enabled

    # Check docstring mentions priority or HAS_LANGFUSE
    assert is_langfuse_enabled.__doc__ is not None
    docstring = is_langfuse_enabled.__doc__.lower()
    assert "has_langfuse" in docstring or "priority" in docstring
