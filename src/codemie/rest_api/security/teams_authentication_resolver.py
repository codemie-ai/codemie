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

"""Isolated helpers that swap the Teams service account for the real end-user.

Mirrors the UserProvider separation-of-concerns pattern: this module is the
entire surface area, since there is exactly one caller (authenticate()) and
no state to hold. Split into a stateless predicate (is_teams_bot_request)
and the actual swap (impersonate_teams_bot_request) so the caller can branch
on the check without paying for the resolution lookup.
"""

from fastapi import Request

from codemie.configs import config, logger
from codemie.configs.customer_config import customer_config
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_type_validator import SERVICE_ACCOUNT_USER_TYPE
from codemie.service.settings.settings_request_validator import TEAMS_BOT_INTEGRATION_FEATURE
from codemie.service.user.authentication_service import authentication_service

TEAMS_SENDER_EMAIL_HEADER = "X-Teams-Sender-Email"


def _is_allowlisted(caller: User) -> bool:
    if not config.TEAMS_SERVICE_ACCOUNT_ID:
        logger.warning("sender_email_ignored: TEAMS_SERVICE_ACCOUNT_ID is not configured; substitution skipped")
        return False

    if caller.user_type != SERVICE_ACCOUNT_USER_TYPE:
        logger.warning(
            f"sender_email_ignored: caller_id={caller.id!r} has user_type={caller.user_type!r}, "
            f"expected {SERVICE_ACCOUNT_USER_TYPE!r}; substitution skipped"
        )
        return False

    if caller.id != config.TEAMS_SERVICE_ACCOUNT_ID:
        logger.warning(
            f"sender_email_ignored: caller_id={caller.id!r} does not match the configured "
            f"Teams service account id={config.TEAMS_SERVICE_ACCOUNT_ID!r}; substitution skipped"
        )
        return False

    return True


def _sender_email(raw_request: Request) -> str:
    # Upstream Teams-relay infrastructure populates this header, not the end user
    # typing it in, so incidental leading/trailing whitespace is plausible and must
    # not cause a legitimate sender to fail the exact-match email lookup below.
    return (raw_request.headers.get(TEAMS_SENDER_EMAIL_HEADER) or "").strip()


def is_teams_bot_request(caller: User, raw_request: Request) -> bool:
    """True when this request should be routed through Teams-sender impersonation.

    True only when the teamsBotIntegration feature flag is on, the sender-email
    header is present, and caller is the allow-listed Teams service account.
    """
    if not customer_config.is_feature_enabled(TEAMS_BOT_INTEGRATION_FEATURE):
        return False

    if not _sender_email(raw_request):
        return False

    return _is_allowlisted(caller)


async def impersonate_teams_bot_request(caller: User, raw_request: Request) -> User:
    """Swap the Teams service account for the real end-user named by the sender-email header.

    Only call this after is_teams_bot_request(caller, raw_request) returned True.

    Known, pre-existing, out-of-scope gap: the header is trusted verbatim once the
    caller matches the allow-listed service account, with no HMAC/signature binding
    it to the real Teams message (see
    docs/superpowers/tasks/2026-08-31-implement-option-4b-for-routing-teams-bot-group-ch/lens-blind.md).
    This plan deliberately does not fix it.
    """
    sender_email = _sender_email(raw_request)

    try:
        resolved = await authentication_service.authenticate_teams_sender(sender_email)
    except Exception:
        logger.warning(
            f"sender_email_ignored: caller_id={caller.id!r} (service account) could not resolve "
            f"sender_email={sender_email!r} to a billable end-user; substitution rejected"
        )
        raise

    logger.info(
        f"teams_sender_impersonated: caller_id={caller.id!r} (service account) is now representing "
        f"resolved_user_id={resolved.id!r} email={resolved.email!r} for this request, resolved from "
        f"sender_email={sender_email!r}"
    )

    return resolved
