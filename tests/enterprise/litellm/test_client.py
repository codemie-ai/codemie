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

"""Tests for LiteLLM HTTP client management (codemie.enterprise.litellm.client)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGetLLMProxyClient:
    """Test get_llm_proxy_client() function."""

    def test_creates_client_on_first_call(self):
        """Test creates httpx.AsyncClient on first call with correct config."""
        # Reset global client
        import codemie.enterprise.litellm.client as client_module

        client_module._llm_proxy_client = None

        mock_async_client = MagicMock()

        with patch("httpx.AsyncClient", return_value=mock_async_client) as mock_client_class:
            with patch("codemie.configs.config") as mock_config:
                mock_config.LITE_LLM_URL = "http://localhost:4000"
                mock_config.LLM_PROXY_TIMEOUT = 300.0

                from codemie.enterprise.litellm.client import get_llm_proxy_client

                result = get_llm_proxy_client()

                # Should have created client with correct parameters
                mock_client_class.assert_called_once_with(
                    base_url="http://localhost:4000",
                    timeout=300.0,
                )

                # Should return the created client
                assert result is mock_async_client

        # Clean up
        client_module._llm_proxy_client = None

    def test_returns_same_client_on_subsequent_calls(self):
        """Test returns the same client instance (singleton pattern)."""
        # Reset global client
        import codemie.enterprise.litellm.client as client_module

        client_module._llm_proxy_client = None

        mock_async_client = MagicMock()

        with patch("httpx.AsyncClient", return_value=mock_async_client) as mock_client_class:
            with patch("codemie.configs.config") as mock_config:
                mock_config.LITE_LLM_URL = "http://localhost:4000"
                mock_config.LLM_PROXY_TIMEOUT = 300.0

                from codemie.enterprise.litellm.client import get_llm_proxy_client

                # First call creates client
                result1 = get_llm_proxy_client()
                # Second call reuses client
                result2 = get_llm_proxy_client()

                # Should have created client only once
                assert mock_client_class.call_count == 1

                # Both calls should return same instance
                assert result1 is result2
                assert result1 is mock_async_client

        # Clean up
        client_module._llm_proxy_client = None

    def test_uses_config_values(self):
        """Test uses config values for base_url and timeout."""
        # Reset global client
        import codemie.enterprise.litellm.client as client_module

        client_module._llm_proxy_client = None

        with patch("httpx.AsyncClient") as mock_client_class:
            with patch("codemie.configs.config") as mock_config:
                # Set custom config values
                mock_config.LITE_LLM_URL = "http://custom-host:8080"
                mock_config.LLM_PROXY_TIMEOUT = 120.0

                from codemie.enterprise.litellm.client import get_llm_proxy_client

                get_llm_proxy_client()

                # Should use custom config values
                mock_client_class.assert_called_once_with(
                    base_url="http://custom-host:8080",
                    timeout=120.0,
                )

        # Clean up
        client_module._llm_proxy_client = None


class TestCloseLLMProxyClient:
    """Test close_llm_proxy_client() function."""

    @pytest.mark.asyncio
    async def test_closes_client_if_initialized(self):
        """Test closes the HTTP client if it was initialized."""
        # Reset and set up global client
        import codemie.enterprise.litellm.client as client_module

        mock_client = AsyncMock()
        client_module._llm_proxy_client = mock_client

        from codemie.enterprise.litellm.client import close_llm_proxy_client

        await close_llm_proxy_client()

        # Should have called aclose on the client
        mock_client.aclose.assert_called_once()

        # Should have reset global client to None
        assert client_module._llm_proxy_client is None

    @pytest.mark.asyncio
    async def test_does_nothing_if_not_initialized(self):
        """Test does nothing if client was never initialized."""
        # Reset global client
        import codemie.enterprise.litellm.client as client_module

        client_module._llm_proxy_client = None

        from codemie.enterprise.litellm.client import close_llm_proxy_client

        # Should not raise exception
        await close_llm_proxy_client()

        # Client should still be None
        assert client_module._llm_proxy_client is None

    @pytest.mark.asyncio
    async def test_handles_multiple_close_calls(self):
        """Test handles multiple close calls gracefully."""
        # Reset and set up global client
        import codemie.enterprise.litellm.client as client_module

        mock_client = AsyncMock()
        client_module._llm_proxy_client = mock_client

        from codemie.enterprise.litellm.client import close_llm_proxy_client

        # First close
        await close_llm_proxy_client()
        # Second close should not raise
        await close_llm_proxy_client()

        # Should have called aclose only once (from first call)
        mock_client.aclose.assert_called_once()

        # Client should be None
        assert client_module._llm_proxy_client is None
