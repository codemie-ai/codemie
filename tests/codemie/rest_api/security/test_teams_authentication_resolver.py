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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.security.teams_authentication_resolver import (
    impersonate_teams_bot_request,
    is_teams_bot_request,
)
from codemie.rest_api.security.user import User


def _caller(user_type="service_account", user_id="codemie-teams-bot"):
    return User(id=user_id, email="bot@svc.example.com", user_type=user_type, project_names=[])


@patch("codemie.rest_api.security.teams_authentication_resolver.customer_config")
def test_no_header_is_not_a_teams_bot_request(mock_customer_config):
    mock_customer_config.is_feature_enabled.return_value = True
    caller = _caller()
    raw_request = MagicMock()
    raw_request.headers = {}
    assert is_teams_bot_request(caller, raw_request) is False


@patch("codemie.rest_api.security.teams_authentication_resolver.customer_config")
def test_feature_off_is_not_a_teams_bot_request(mock_customer_config):
    mock_customer_config.is_feature_enabled.return_value = False
    caller = _caller()
    raw_request = MagicMock()
    raw_request.headers = {"X-Teams-Sender-Email": "enduser@example.com"}
    assert is_teams_bot_request(caller, raw_request) is False


@patch("codemie.rest_api.security.teams_authentication_resolver.config")
@patch("codemie.rest_api.security.teams_authentication_resolver.customer_config")
def test_non_allowlisted_user_type_is_not_a_teams_bot_request(mock_customer_config, mock_config):
    mock_customer_config.is_feature_enabled.return_value = True
    mock_config.TEAMS_SERVICE_ACCOUNT_ID = "codemie-teams-bot"
    caller = _caller(user_type="human")
    raw_request = MagicMock()
    raw_request.headers = {"X-Teams-Sender-Email": "enduser@example.com"}
    assert is_teams_bot_request(caller, raw_request) is False


@patch("codemie.rest_api.security.teams_authentication_resolver.config")
@patch("codemie.rest_api.security.teams_authentication_resolver.customer_config")
def test_non_allowlisted_id_is_not_a_teams_bot_request(mock_customer_config, mock_config):
    mock_customer_config.is_feature_enabled.return_value = True
    mock_config.TEAMS_SERVICE_ACCOUNT_ID = "codemie-teams-bot"
    caller = _caller(user_id="some-other-service-account")
    raw_request = MagicMock()
    raw_request.headers = {"X-Teams-Sender-Email": "enduser@example.com"}
    assert is_teams_bot_request(caller, raw_request) is False


@patch("codemie.rest_api.security.teams_authentication_resolver.customer_config")
def test_header_of_only_whitespace_is_not_a_teams_bot_request(mock_customer_config):
    mock_customer_config.is_feature_enabled.return_value = True
    caller = _caller()
    raw_request = MagicMock()
    raw_request.headers = {"X-Teams-Sender-Email": "   "}
    assert is_teams_bot_request(caller, raw_request) is False


@patch("codemie.rest_api.security.teams_authentication_resolver.config")
@patch("codemie.rest_api.security.teams_authentication_resolver.customer_config")
def test_allowlisted_caller_with_header_is_a_teams_bot_request(mock_customer_config, mock_config):
    mock_customer_config.is_feature_enabled.return_value = True
    mock_config.TEAMS_SERVICE_ACCOUNT_ID = "codemie-teams-bot"
    caller = _caller()
    raw_request = MagicMock()
    raw_request.headers = {"X-Teams-Sender-Email": "enduser@example.com"}
    assert is_teams_bot_request(caller, raw_request) is True


@pytest.mark.asyncio
@patch("codemie.rest_api.security.teams_authentication_resolver.authentication_service")
async def test_successful_impersonation_returns_resolved_user(mock_auth_service):
    resolved = User(id="end-user-1", email="enduser@example.com", user_type="human", project_names=[])
    mock_auth_service.authenticate_teams_sender = AsyncMock(return_value=resolved)

    caller = _caller()
    raw_request = MagicMock()
    raw_request.headers = {"X-Teams-Sender-Email": "enduser@example.com"}

    result = await impersonate_teams_bot_request(caller, raw_request)

    assert result is resolved
    mock_auth_service.authenticate_teams_sender.assert_awaited_once_with("enduser@example.com")


@pytest.mark.asyncio
@patch("codemie.rest_api.security.teams_authentication_resolver.authentication_service")
async def test_sender_email_header_whitespace_is_trimmed_before_lookup(mock_auth_service):
    """CR-003: incidental leading/trailing whitespace on the header (plausible since
    it is populated by upstream Teams-relay infrastructure) must not reach the
    exact-match email lookup verbatim.
    """
    resolved = User(id="end-user-1", email="enduser@example.com", user_type="human", project_names=[])
    mock_auth_service.authenticate_teams_sender = AsyncMock(return_value=resolved)

    caller = _caller()
    raw_request = MagicMock()
    raw_request.headers = {"X-Teams-Sender-Email": "  enduser@example.com \t"}

    result = await impersonate_teams_bot_request(caller, raw_request)

    assert result is resolved
    mock_auth_service.authenticate_teams_sender.assert_awaited_once_with("enduser@example.com")


@pytest.mark.asyncio
@patch("codemie.rest_api.security.teams_authentication_resolver.logger")
@patch("codemie.rest_api.security.teams_authentication_resolver.authentication_service")
async def test_successful_impersonation_is_audit_logged(mock_auth_service, mock_logger):
    """CR-004: a successful identity swap must leave an audit trail, equivalent to
    the deleted BillingUserResolver's billing_user_impersonated log line.
    """
    resolved = User(id="end-user-1", email="enduser@example.com", user_type="human", project_names=[])
    mock_auth_service.authenticate_teams_sender = AsyncMock(return_value=resolved)

    caller = _caller()
    raw_request = MagicMock()
    raw_request.headers = {"X-Teams-Sender-Email": "enduser@example.com"}

    await impersonate_teams_bot_request(caller, raw_request)

    logged_messages = [call.args[0] for call in mock_logger.info.call_args_list]
    assert any("teams_sender_impersonated" in msg and "end-user-1" in msg for msg in logged_messages)


@patch("codemie.rest_api.security.teams_authentication_resolver.logger")
@patch("codemie.rest_api.security.teams_authentication_resolver.config")
@patch("codemie.rest_api.security.teams_authentication_resolver.customer_config")
def test_rejected_request_is_audit_logged(mock_customer_config, mock_config, mock_logger):
    """CR-004: a rejected request (caller not allow-listed) must leave an audit
    trail, equivalent to the deleted BillingUserResolver's sender_email_ignored log line.
    """
    mock_customer_config.is_feature_enabled.return_value = True
    mock_config.TEAMS_SERVICE_ACCOUNT_ID = "codemie-teams-bot"
    caller = _caller(user_type="human")
    raw_request = MagicMock()
    raw_request.headers = {"X-Teams-Sender-Email": "enduser@example.com"}

    result = is_teams_bot_request(caller, raw_request)

    assert result is False
    logged_messages = [call.args[0] for call in mock_logger.warning.call_args_list]
    assert any("sender_email_ignored" in msg for msg in logged_messages)
