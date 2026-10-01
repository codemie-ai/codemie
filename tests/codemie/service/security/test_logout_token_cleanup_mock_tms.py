# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Logout against a real (mock-backed) TMS: the user's rows are gone, other users' rows survive."""

from __future__ import annotations

from contextlib import nullcontext

import pytest

from codemie.enterprise.mcp_auth import dependencies
from codemie.service.security import logout_token_cleanup
from codemie.service.security.tms_token_store import TMSTokenStore
from codemie.service.security.token_exchange_service import TokenExchangeService

mcp_auth = pytest.importorskip("codemie_enterprise.mcp_auth")

_USER_A = "user-a"
_USER_B = "user-b"
_MCP_CONFIG_ID = "mcp-config-1"
_TOOL_INTEGRATION_ID = "tool-integration-1"
_CONFIG_IDS = ("__idp_token__", _MCP_CONFIG_ID, _TOOL_INTEGRATION_ID)


class _NoAudit:
    def context(self, *, source, correlation_id=None):
        return nullcontext()


@pytest.fixture
def tms(monkeypatch):
    mock_tms = mcp_auth.MockTokenManagementSystem()
    monkeypatch.setattr(dependencies, "_tms", mock_tms, raising=False)
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    for user_id in (_USER_A, _USER_B):
        for config_id in _CONFIG_IDS:
            mock_tms.store(user_id, config_id, mcp_auth.OAuth2TokenData(access_token=f"{user_id}-{config_id}"))
    return mock_tms


@pytest.fixture
def exchange_store(monkeypatch):
    store = TMSTokenStore(None, _NoAudit())
    monkeypatch.setattr(TokenExchangeService, "_store", store)
    return store


def test_logout_removes_only_the_users_rows_and_pod_fallback(tms, exchange_store):
    exchange_store._fallback[f"{_USER_A}:{_MCP_CONFIG_ID}"] = "stale-a"
    exchange_store._fallback[f"{_USER_B}:{_MCP_CONFIG_ID}"] = "stale-b"

    logout_token_cleanup.remove_user_tokens_on_logout(_USER_A)

    for config_id in _CONFIG_IDS:
        with pytest.raises(mcp_auth.TokenNotFound):
            tms.retrieve(_USER_A, config_id)
        assert tms.retrieve(_USER_B, config_id).access_token == f"{_USER_B}-{config_id}"
    assert dict(exchange_store._fallback) == {f"{_USER_B}:{_MCP_CONFIG_ID}": "stale-b"}
