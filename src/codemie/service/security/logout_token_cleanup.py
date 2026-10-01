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

"""Best-effort removal of a user's Token Management System (TMS) tokens on explicit logout.

Logout must complete whatever happens here, so ``remove_user_tokens_on_logout`` never raises and
never retries, enqueues or schedules anything. A failed delete leaves the rows in TMS until they
expire; the sanitized warning is the only trace.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.service.oauth_security import is_tool_oauth_enabled
from codemie.service.security import tms_vault_ops
from codemie.service.security.oidc_token_exchange_service import oidc_token_exchange_service
from codemie.service.security.token_exchange_service import token_exchange_service

_AUDIT_SOURCE = "user_logout"


def remove_user_tokens_on_logout(user_id: str) -> None:
    """Delete every TMS row held for ``user_id`` and purge this pod's per-user token caches.

    Each step is contained on its own: one failing step neither skips the steps after it nor hides
    another step's failure. Logs the user id and the exception class name only: exception text can
    carry token material.
    """
    try:
        tms_in_use = _tms_in_use()
    except Exception as exc:
        _log_step_failure("check whether TMS is in use", user_id, exc)
        return
    if not tms_in_use:
        return
    _attempt("delete TMS tokens", user_id, _delete_all_tms_tokens)
    _attempt("purge token exchange cache", user_id, token_exchange_service.purge_user_cache)
    _attempt("purge OIDC exchange cache", user_id, oidc_token_exchange_service.purge_user_cache)


async def remove_user_tokens_on_logout_async(user_id: str) -> None:
    """Run the cleanup off the event loop, giving up after ``LOGOUT_TOKEN_CLEANUP_TIMEOUT_SECONDS``.

    A stalled TMS, Redis or database call must not hang the logout response. The worker thread cannot
    be cancelled: on timeout it keeps running and its result is dropped.
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(remove_user_tokens_on_logout, user_id),
            timeout=config.LOGOUT_TOKEN_CLEANUP_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(f"Logout token cleanup timed out for user_id={user_id}")


def _tms_in_use() -> bool:
    """MCP auth and tool OAuth are the only TMS writers.

    ``get_token_management_system()`` is no test: it builds a TMS on first call and raises when the
    enterprise package is absent.
    """
    from codemie.enterprise.mcp_auth.dependencies import is_mcp_auth_enabled

    return is_mcp_auth_enabled() or is_tool_oauth_enabled()


def _delete_all_tms_tokens(user_id: str) -> None:
    from codemie.enterprise.mcp_auth.dependencies import get_token_management_system, tms_audit_context

    tms = get_token_management_system()
    tms_vault_ops.delete_all_for_user(tms, tms_audit_context(_AUDIT_SOURCE, user_id), user_id)


def _attempt(step: str, user_id: str, action: Callable[[str], None]) -> None:
    try:
        action(user_id)
    except Exception as exc:
        _log_step_failure(step, user_id, exc)


def _log_step_failure(step: str, user_id: str, exc: Exception) -> None:
    logger.warning(f"Logout token cleanup failed to {step} for user_id={user_id}: {type(exc).__name__}")
