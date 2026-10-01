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

"""remove_user_tokens_on_logout: deletes the user's TMS rows, purges pod caches, never raises."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

import codemie.service.oauth_security as oauth_security
from codemie.configs import config
from codemie.enterprise.mcp_auth import dependencies
from codemie.service.security import logout_token_cleanup

_USER_ID = "user-1"
_SECRET = "sentinel-secret-token-value"


class TMSUnavailable(Exception):
    pass


class TMSAuditError(Exception):
    pass


@pytest.fixture
def bridge(monkeypatch):
    """Patch the enterprise bridge: MCP auth on, a fake TMS handle, a fake audit context."""
    mocks = MagicMock()
    mocks.is_mcp_auth_enabled.return_value = True
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", mocks.is_mcp_auth_enabled)
    monkeypatch.setattr(dependencies, "get_token_management_system", mocks.get_token_management_system)
    monkeypatch.setattr(dependencies, "tms_audit_context", mocks.tms_audit_context)
    monkeypatch.setattr(dependencies, "enqueue_mcp_auth_cleanup", mocks.enqueue_mcp_auth_cleanup)
    return mocks


@pytest.fixture
def delete_all_for_user(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(logout_token_cleanup.tms_vault_ops, "delete_all_for_user", mock)
    return mock


@pytest.fixture
def purges(monkeypatch):
    mocks = MagicMock()
    monkeypatch.setattr(logout_token_cleanup, "token_exchange_service", mocks.token_exchange_service)
    monkeypatch.setattr(logout_token_cleanup, "oidc_token_exchange_service", mocks.oidc_token_exchange_service)
    return mocks


@pytest.fixture
def warning(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(logout_token_cleanup.logger, "warning", mock)
    return mock


def _assert_caches_purged(purges) -> None:
    purges.token_exchange_service.purge_user_cache.assert_called_once_with(_USER_ID)
    purges.oidc_token_exchange_service.purge_user_cache.assert_called_once_with(_USER_ID)


def test_success_deletes_all_rows_under_logout_audit_context_and_purges_caches(
    bridge, delete_all_for_user, purges, warning
):
    logout_token_cleanup.remove_user_tokens_on_logout(_USER_ID)

    bridge.tms_audit_context.assert_called_once_with("user_logout", _USER_ID)
    delete_all_for_user.assert_called_once_with(
        bridge.get_token_management_system.return_value,
        bridge.tms_audit_context.return_value,
        _USER_ID,
    )
    _assert_caches_purged(purges)
    warning.assert_not_called()


@pytest.mark.parametrize("failure", [TMSUnavailable(_SECRET), TMSAuditError(_SECRET)])
def test_delete_failure_is_contained_and_still_purges_caches(bridge, delete_all_for_user, purges, warning, failure):
    delete_all_for_user.side_effect = failure

    logout_token_cleanup.remove_user_tokens_on_logout(_USER_ID)

    _assert_caches_purged(purges)
    bridge.enqueue_mcp_auth_cleanup.assert_not_called()
    _assert_single_sanitized_warning(warning, type(failure).__name__)


def test_purge_failure_does_not_skip_the_next_purge_or_hide_the_delete_failure(
    bridge, delete_all_for_user, purges, warning
):
    delete_all_for_user.side_effect = TMSUnavailable(_SECRET)
    purges.token_exchange_service.purge_user_cache.side_effect = RuntimeError(_SECRET)

    logout_token_cleanup.remove_user_tokens_on_logout(_USER_ID)

    purges.oidc_token_exchange_service.purge_user_cache.assert_called_once_with(_USER_ID)
    messages = [call.args[0] for call in warning.call_args_list]
    assert len(messages) == 2
    assert any("TMSUnavailable" in message for message in messages)
    assert any("RuntimeError" in message for message in messages)
    assert all(_USER_ID in message and _SECRET not in message for message in messages)


def test_handle_lookup_failure_is_contained_and_still_purges_caches(bridge, delete_all_for_user, purges, warning):
    bridge.get_token_management_system.side_effect = RuntimeError(_SECRET)

    logout_token_cleanup.remove_user_tokens_on_logout(_USER_ID)

    delete_all_for_user.assert_not_called()
    _assert_caches_purged(purges)
    bridge.enqueue_mcp_auth_cleanup.assert_not_called()
    _assert_single_sanitized_warning(warning, "RuntimeError")


def test_feature_probe_failure_is_contained(bridge, delete_all_for_user, purges, warning):
    bridge.is_mcp_auth_enabled.side_effect = RuntimeError(_SECRET)

    logout_token_cleanup.remove_user_tokens_on_logout(_USER_ID)

    bridge.get_token_management_system.assert_not_called()
    _assert_single_sanitized_warning(warning, "RuntimeError")


def test_both_features_off_is_a_silent_no_op(bridge, delete_all_for_user, purges, warning, monkeypatch):
    bridge.is_mcp_auth_enabled.return_value = False
    monkeypatch.setattr(logout_token_cleanup, "is_tool_oauth_enabled", lambda: False)

    logout_token_cleanup.remove_user_tokens_on_logout(_USER_ID)

    bridge.get_token_management_system.assert_not_called()
    delete_all_for_user.assert_not_called()
    purges.token_exchange_service.purge_user_cache.assert_not_called()
    warning.assert_not_called()


def test_tool_oauth_alone_counts_as_tms_in_use(bridge, delete_all_for_user, purges, monkeypatch):
    bridge.is_mcp_auth_enabled.return_value = False
    monkeypatch.setattr(logout_token_cleanup, "is_tool_oauth_enabled", lambda: True)

    logout_token_cleanup.remove_user_tokens_on_logout(_USER_ID)

    delete_all_for_user.assert_called_once()
    _assert_caches_purged(purges)


@pytest.mark.parametrize("flag", ["GITLAB_OAUTH_ENABLED", "JIRA_OAUTH_ENABLED", "CONFLUENCE_OAUTH_ENABLED"])
def test_is_tool_oauth_enabled_is_true_for_any_single_flag(monkeypatch, flag):
    for name in ("GITLAB_OAUTH_ENABLED", "JIRA_OAUTH_ENABLED", "CONFLUENCE_OAUTH_ENABLED"):
        monkeypatch.setattr(oauth_security.config, name, name == flag, raising=False)

    assert oauth_security.is_tool_oauth_enabled() is True


def test_is_tool_oauth_enabled_is_false_when_all_flags_off(monkeypatch):
    for name in ("GITLAB_OAUTH_ENABLED", "JIRA_OAUTH_ENABLED", "CONFLUENCE_OAUTH_ENABLED"):
        monkeypatch.setattr(oauth_security.config, name, False, raising=False)

    assert oauth_security.is_tool_oauth_enabled() is False


def _assert_single_sanitized_warning(warning: MagicMock, exception_name: str) -> None:
    warning.assert_called_once()
    message = warning.call_args.args[0]
    assert _USER_ID in message
    assert exception_name in message
    assert _SECRET not in message
    assert not warning.call_args.kwargs


@pytest.mark.asyncio
async def test_async_cleanup_runs_the_unit_off_the_event_loop(monkeypatch):
    calls = []
    monkeypatch.setattr(
        logout_token_cleanup,
        "remove_user_tokens_on_logout",
        lambda user_id: calls.append((user_id, threading.get_ident())),
    )

    await logout_token_cleanup.remove_user_tokens_on_logout_async(_USER_ID)

    assert [user_id for user_id, _ in calls] == [_USER_ID]
    assert calls[0][1] != threading.get_ident()


@pytest.mark.asyncio
async def test_async_cleanup_gives_up_after_the_timeout_and_warns(monkeypatch, warning):
    """A stalled TMS must not hang the logout response; the worker thread is left to finish on its own."""
    release = threading.Event()
    monkeypatch.setattr(logout_token_cleanup, "remove_user_tokens_on_logout", lambda user_id: release.wait(1))
    monkeypatch.setattr(config, "LOGOUT_TOKEN_CLEANUP_TIMEOUT_SECONDS", 0.05)

    try:
        await logout_token_cleanup.remove_user_tokens_on_logout_async(_USER_ID)
    finally:
        release.set()

    warning.assert_called_once()
    message = warning.call_args.args[0]
    assert _USER_ID in message
    assert "timed out" in message
