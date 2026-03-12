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

from __future__ import annotations

# Lazy-initialized HTTP client for LiteLLM proxy communication
_llm_proxy_client = None


def get_llm_proxy_client():
    """
    Get or create HTTP client for LiteLLM proxy (lazy initialization).

    This ensures the client is only created when actually needed,
    avoiding connection attempts when LiteLLM enterprise is not available.

    The client is used by proxy endpoints to forward requests to LiteLLM proxy server.

    Returns:
        httpx.AsyncClient configured for LiteLLM proxy

    Usage:
        from codemie.enterprise.litellm import get_llm_proxy_client

        client = get_llm_proxy_client()
        response = await client.send(request)
    """
    import httpx

    from codemie.configs import config

    global _llm_proxy_client
    if _llm_proxy_client is None:
        _llm_proxy_client = httpx.AsyncClient(
            base_url=config.LITE_LLM_URL,
            timeout=config.LLM_PROXY_TIMEOUT,
        )
    return _llm_proxy_client


async def close_llm_proxy_client() -> None:
    """
    Close the LiteLLM proxy HTTP client if it was initialized.

    Should be called during application shutdown to properly close
    the HTTP connection pool and free resources.

    Usage:
        from codemie.enterprise.litellm import close_llm_proxy_client

        # In main.py lifespan cleanup:
        await close_llm_proxy_client()
    """
    global _llm_proxy_client
    if _llm_proxy_client is not None:
        await _llm_proxy_client.aclose()
        _llm_proxy_client = None
