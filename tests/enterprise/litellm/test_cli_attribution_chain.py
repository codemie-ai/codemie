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

"""The CLI/IDE proxy chain end to end around budget attribution (EPMCDME-16625 review AR-1, AR-2).

Each test runs the real steps in their real order and patches only storage and LiteLLM.
"""

import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.enterprise.litellm.budget_categories import BudgetCategory
from codemie.rest_api.security.user import User
from codemie.service.monitoring.metrics_constants import MetricsAttributes

PROXY_ROUTER = "codemie.enterprise.litellm.proxy_router"
# The package re-exports its APIRouter under the module's name, so the module is imported by path.
proxy_router = importlib.import_module(PROXY_ROUTER)
FOREIGN_PROJECT = "team-b"
HOME_PROJECT = "home"


@asynccontextmanager
async def _no_session():
    yield MagicMock()


def _admin_non_member() -> User:
    return User(
        id="u-admin",
        username="admin",
        email="admin@example.com",
        project_names=[HOME_PROJECT],
        admin_project_names=[],
        default_project=HOME_PROJECT,
        is_admin=True,
    )


async def _authorized_request_info(user: User, header_project: str) -> dict:
    """Header filter then project check, as the proxy entry point runs them."""
    headers = {proxy_router.HEADER_CODEMIE_CLI_PROJECT: header_project}
    request_info = proxy_router._extract_request_info(headers, user)
    application = SimpleNamespace(name=header_project)
    with (
        patch(f"{PROXY_ROUTER}.get_async_session", new=_no_session),
        patch(
            "codemie.repository.application_repository.application_repository.aget_active_by_name_case_insensitive",
            new=AsyncMock(return_value=application),
        ),
        patch(
            "codemie.repository.user_project_repository.user_project_repository.aget_by_user_and_project",
            new=AsyncMock(return_value=MagicMock()),
        ),
    ):
        await proxy_router._authorize_project_header(headers, request_info, user)
    return request_info


class TestAnAdminWhoIsNotAMemberNeverRunsOnTheNamedProjectsKey:
    """AR-2: the project check let the header project back in for anyone allowed to use it, so the
    credentials lookup returned that project's key while logs said the header was ignored."""

    @pytest.fixture(autouse=True)
    def _admin_flag_comes_from_the_database(self, monkeypatch):
        # User.resolve_is_admin recomputes is_admin from config: off ENV=local with user management off it
        # drops the flag passed in, so a test that left either setting changed turned this admin into a
        # regular user. Pin the production mode where is_admin comes from the user row.
        from codemie.configs import config

        monkeypatch.setattr(config, "ENV", "development")
        monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)

    @pytest.mark.asyncio
    async def test_the_project_check_keeps_the_admins_own_project(self):
        request_info = await _authorized_request_info(_admin_non_member(), FOREIGN_PROJECT)

        assert request_info[proxy_router.PROJECT] == HOME_PROJECT

    @pytest.mark.asyncio
    async def test_credentials_are_looked_up_for_the_admins_project_and_the_app_key_authenticates(self):
        user = _admin_non_member()
        request_info = await _authorized_request_info(user, FOREIGN_PROJECT)
        request_info[proxy_router.LLM_MODEL] = "claude-opus-premium"
        lookup = MagicMock(return_value=None)

        with patch(f"{PROXY_ROUTER}.resolve_litellm_user_credentials", lookup):
            credentials = proxy_router.resolve_litellm_user_credentials(
                user_id=user.id,
                username=user.username,
                project_name=request_info.get(proxy_router.PROJECT),
                llm_model=request_info.get(proxy_router.LLM_MODEL),
                integration_id=request_info.get(proxy_router.INTEGRATION),
            )
        headers: dict = {}
        proxy_router._apply_proxy_auth_header(headers, request_info, credentials)

        assert lookup.call_args.kwargs["project_name"] == HOME_PROJECT
        assert (
            headers["Authorization"]
            == f"Bearer {proxy_router.config.LITE_LLM_PROXY_APP_KEY or proxy_router.config.LITE_LLM_APP_KEY}"
        )

    @pytest.mark.asyncio
    async def test_attribution_logs_the_header_as_ignored_and_charges_the_admins_default(self):
        user = _admin_non_member()
        request_info = await _authorized_request_info(user, FOREIGN_PROJECT)
        request_info[proxy_router.LLM_MODEL] = "gpt-4.1"
        request_info[proxy_router.CLIENT_TYPE] = "codemie-cli"

        with (
            patch(
                f"{PROXY_ROUTER}._probe_project_budget_scopes",
                new=AsyncMock(return_value={BudgetCategory.CLI}),
            ),
            patch(
                f"{PROXY_ROUTER}.budget_service.get_all_category_budget_ids_for_request",
                new=AsyncMock(return_value={}),
            ),
        ):
            decision = await proxy_router._apply_budget_attribution(user, request_info)

        assert decision.ignored_cli_project == FOREIGN_PROJECT
        assert decision.charged_project == HOME_PROJECT
        assert request_info[proxy_router.PROJECT] == HOME_PROJECT

    @pytest.mark.asyncio
    async def test_a_member_still_gets_the_stored_spelling(self):
        member = User(
            id="u-1",
            username="alice",
            email="alice@example.com",
            project_names=[FOREIGN_PROJECT],
            default_project=FOREIGN_PROJECT,
        )

        request_info = await _authorized_request_info(member, FOREIGN_PROJECT)

        assert request_info[proxy_router.PROJECT] == FOREIGN_PROJECT


class TestARegularUserNamingAProjectTheyCannotUseIsRefused:
    """Ticket ACs as revised after EPMCDME-16276: a non-member project header is answered with 403, nothing is
    charged."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("application", [SimpleNamespace(name=FOREIGN_PROJECT), None])
    async def test_403_for_a_project_the_user_is_not_a_member_of_or_that_does_not_exist(self, application):
        from codemie.configs import config
        from codemie.core.exceptions import ExtendedHTTPException

        # ENV=local makes every user an admin; this user must be a regular one.
        with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
            user = User(
                id="u-1",
                username="alice",
                email="alice@example.com",
                project_names=[HOME_PROJECT],
                default_project=HOME_PROJECT,
            )
        assert not user.is_admin_or_maintainer
        headers = {proxy_router.HEADER_CODEMIE_CLI_PROJECT: FOREIGN_PROJECT}
        request_info = proxy_router._extract_request_info(headers, user)
        with (
            patch(f"{PROXY_ROUTER}.get_async_session", new=_no_session),
            patch(
                "codemie.repository.application_repository.application_repository.aget_active_by_name_case_insensitive",
                new=AsyncMock(return_value=application),
            ),
            pytest.raises(ExtendedHTTPException) as refused,
        ):
            await proxy_router._authorize_project_header(headers, request_info, user)

        assert refused.value.code == 403


class TestTheCliStreamTriesTheDefaultProjectBeforeThePersonalBudget:
    """AR-1: when the charged project has no runtime for the request, the default project is the next step."""

    @pytest.mark.asyncio
    async def test_a_project_without_a_runtime_hands_over_to_the_default(self):
        user = User(
            id="u-1",
            username="alice",
            email="alice@example.com",
            project_names=["team-a", "team-d"],
            default_project="team-d",
        )
        request_info = {
            proxy_router.REQUEST_ID: "req-1",
            proxy_router.LLM_MODEL: "gpt-4.1",
            proxy_router.CLIENT_TYPE: "codemie-cli",
            proxy_router.PROJECT: "team-a",
            proxy_router.CLI_REQUESTED_PROJECT: "team-a",
        }
        default_runtime = SimpleNamespace(headers={})

        async def runtime_for(*, user, category, request_info):
            return default_runtime if request_info[proxy_router.PROJECT] == "team-d" else None

        stream = AsyncMock(return_value="stream")
        with (
            patch(f"{PROXY_ROUTER}._probe_project_budget_scopes", new=AsyncMock(return_value={BudgetCategory.CLI})),
            patch(
                f"{PROXY_ROUTER}.budget_service.get_all_category_budget_ids_for_request",
                new=AsyncMock(return_value={}),
            ),
            patch(f"{PROXY_ROUTER}._resolve_project_budget_runtime", new=runtime_for),
            patch(f"{PROXY_ROUTER}._create_project_runtime_body_stream", new=stream),
        ):
            result = await proxy_router._resolve_budget_attribution_stream(
                body_bytes=b"{}", user=user, request_info=request_info
            )

        assert result == "stream"
        assert stream.call_args.kwargs["project_name"] == "team-d"
        assert request_info[proxy_router.SPEND_ATTRIBUTION_PROJECT] == "team-d"
        assert request_info[MetricsAttributes.BUDGET_FALLBACK_FROM] == "team-a"
        assert request_info[MetricsAttributes.BUDGET_FALLBACK_KIND] == "default_project"

    @pytest.mark.asyncio
    async def test_after_a_failed_funded_check_the_target_is_tried_again(self):
        """Review AR-2: a database error at decision time must not move the charge as web does not."""
        user = User(
            id="u-1",
            username="alice",
            email="alice@example.com",
            project_names=["team-a", "team-d"],
            default_project="team-d",
        )
        request_info = {
            proxy_router.REQUEST_ID: "req-1",
            proxy_router.LLM_MODEL: "gpt-4.1",
            proxy_router.CLIENT_TYPE: "codemie-cli",
            proxy_router.CLI_REQUESTED_PROJECT: "team-a",
        }
        probes = AsyncMock(side_effect=[RuntimeError("db blip"), RuntimeError("db blip"), {BudgetCategory.CLI}])

        async def runtime_for(*, user, category, request_info):
            return SimpleNamespace(headers={}) if request_info[proxy_router.PROJECT] == "team-a" else None

        stream = AsyncMock(return_value="stream")
        with (
            patch(f"{PROXY_ROUTER}._probe_project_budget_scopes", new=probes),
            patch(
                f"{PROXY_ROUTER}.budget_service.get_all_category_budget_ids_for_request",
                new=AsyncMock(return_value={}),
            ),
            patch(f"{PROXY_ROUTER}._resolve_project_budget_runtime", new=runtime_for),
            patch(f"{PROXY_ROUTER}._create_project_runtime_body_stream", new=stream),
        ):
            result = await proxy_router._resolve_budget_attribution_stream(
                body_bytes=b"{}", user=user, request_info=request_info
            )

        assert result == "stream"
        assert stream.call_args.kwargs["project_name"] == "team-a"
        assert request_info[proxy_router.SPEND_ATTRIBUTION_PROJECT] == "team-a"
        assert MetricsAttributes.BUDGET_FALLBACK_FROM not in request_info
        assert MetricsAttributes.BUDGET_FALLBACK_KIND not in request_info
        # Code review CR-003: the target paid, so this is no fallback and is not flagged, as on the web path.
        assert MetricsAttributes.BUDGET_FUNDING_UNKNOWN not in request_info

    @pytest.mark.asyncio
    async def test_the_users_budget_ids_are_read_once_per_request(self):
        """Final review AR-6: a failing lookup is not cached, so each step would otherwise wait for it again."""
        user = User(
            id="u-1",
            username="alice",
            email="alice@example.com",
            project_names=["team-a", "team-d"],
            default_project="team-d",
        )
        request_info = {
            proxy_router.REQUEST_ID: "req-1",
            proxy_router.LLM_MODEL: "gpt-4.1",
            proxy_router.CLIENT_TYPE: "codemie-cli",
            proxy_router.CLI_REQUESTED_PROJECT: "team-a",
        }
        probes = AsyncMock(side_effect=[RuntimeError("db blip"), RuntimeError("db blip"), {BudgetCategory.CLI}])
        budget_ids = AsyncMock(return_value={})

        async def runtime_for(*, user, category, request_info):
            return SimpleNamespace(headers={})

        with (
            patch(f"{PROXY_ROUTER}._probe_project_budget_scopes", new=probes),
            patch(f"{PROXY_ROUTER}.budget_service.get_all_category_budget_ids_for_request", new=budget_ids),
            patch(f"{PROXY_ROUTER}._resolve_project_budget_runtime", new=runtime_for),
            patch(f"{PROXY_ROUTER}._create_project_runtime_body_stream", new=AsyncMock(return_value="stream")),
        ):
            await proxy_router._resolve_budget_attribution_stream(
                body_bytes=b"{}", user=user, request_info=request_info
            )

        assert probes.await_count == 3
        budget_ids.assert_awaited_once_with("u-1")

    @pytest.mark.asyncio
    async def test_a_fallback_decided_while_funding_was_unreadable_is_flagged(self):
        """Code review CR-005: the CLI half of the filterable flag (final review AR-2)."""
        user = User(
            id="u-1",
            username="alice",
            email="alice@example.com",
            project_names=["team-a", "team-d"],
            default_project="team-d",
        )
        request_info = {
            proxy_router.LLM_MODEL: "gpt-4.1",
            proxy_router.CLIENT_TYPE: "codemie-cli",
            proxy_router.CLI_REQUESTED_PROJECT: "team-a",
        }
        probes = AsyncMock(side_effect=[RuntimeError("db blip"), {BudgetCategory.CLI}])

        with (
            patch(f"{PROXY_ROUTER}._probe_project_budget_scopes", new=probes),
            patch(
                f"{PROXY_ROUTER}.budget_service.get_all_category_budget_ids_for_request",
                new=AsyncMock(return_value={}),
            ),
        ):
            decision = await proxy_router._apply_budget_attribution(user, request_info)

        assert decision.charged_project == "team-d"
        assert request_info[MetricsAttributes.BUDGET_FUNDING_UNKNOWN] is True
        assert request_info[MetricsAttributes.BUDGET_FALLBACK_KIND] == "default_project"

    @pytest.mark.asyncio
    async def test_readable_funding_is_not_flagged(self):
        user = User(
            id="u-1",
            username="alice",
            email="alice@example.com",
            project_names=["team-a", "team-d"],
            default_project="team-d",
        )
        request_info = {
            proxy_router.LLM_MODEL: "gpt-4.1",
            proxy_router.CLIENT_TYPE: "codemie-cli",
            proxy_router.CLI_REQUESTED_PROJECT: "team-a",
        }

        with (
            patch(
                f"{PROXY_ROUTER}._probe_project_budget_scopes", new=AsyncMock(side_effect=[set(), {BudgetCategory.CLI}])
            ),
            patch(
                f"{PROXY_ROUTER}.budget_service.get_all_category_budget_ids_for_request",
                new=AsyncMock(return_value={}),
            ),
        ):
            await proxy_router._apply_budget_attribution(user, request_info)

        assert request_info[MetricsAttributes.BUDGET_FALLBACK_KIND] == "default_project"
        assert MetricsAttributes.BUDGET_FUNDING_UNKNOWN not in request_info

    @pytest.mark.asyncio
    async def test_a_pool_checkout_timeout_in_the_runtime_walk_fails_with_503(self):
        """Code review CR-004: a step the decision walk did not probe fails like the decision walk."""
        from sqlalchemy.exc import TimeoutError as PoolTimeoutError

        from codemie.core.exceptions import ExtendedHTTPException

        user = User(
            id="u-1",
            username="alice",
            email="alice@example.com",
            project_names=["team-a", "team-d"],
            default_project="team-d",
        )
        request_info = {
            proxy_router.REQUEST_ID: "req-1",
            proxy_router.LLM_MODEL: "gpt-4.1",
            proxy_router.CLIENT_TYPE: "codemie-cli",
            proxy_router.CLI_REQUESTED_PROJECT: "team-a",
        }
        # The decision probe funds the target; the runtime walk then finds no target runtime and probes team-d.
        probes = AsyncMock(
            side_effect=[{BudgetCategory.CLI}, {BudgetCategory.CLI}, PoolTimeoutError("QueuePool limit reached")]
        )

        async def runtime_for(*, user, category, request_info):
            return None

        with (
            patch(f"{PROXY_ROUTER}._probe_project_budget_scopes", new=probes),
            patch(
                f"{PROXY_ROUTER}.budget_service.get_all_category_budget_ids_for_request",
                new=AsyncMock(return_value={}),
            ),
            patch(f"{PROXY_ROUTER}._resolve_project_budget_runtime", new=runtime_for),
            pytest.raises(ExtendedHTTPException) as raised,
        ):
            await proxy_router._resolve_budget_attribution_stream(
                body_bytes=b"{}", user=user, request_info=request_info
            )

        assert raised.value.code == 503

    @pytest.mark.asyncio
    async def test_a_pool_checkout_timeout_fails_the_request_at_once(self):
        """Final review AR-6: every later step would wait for the same saturated pool, so nothing else is probed."""
        from sqlalchemy.exc import TimeoutError as PoolTimeoutError

        from codemie.core.exceptions import ExtendedHTTPException

        user = User(
            id="u-1",
            username="alice",
            email="alice@example.com",
            project_names=["team-a", "team-d"],
            default_project="team-d",
        )
        request_info = {
            proxy_router.LLM_MODEL: "gpt-4.1",
            proxy_router.CLIENT_TYPE: "codemie-cli",
            proxy_router.CLI_REQUESTED_PROJECT: "team-a",
        }
        probes = AsyncMock(side_effect=PoolTimeoutError("QueuePool limit reached"))

        with (
            patch(f"{PROXY_ROUTER}._probe_project_budget_scopes", new=probes),
            patch(
                f"{PROXY_ROUTER}.budget_service.get_all_category_budget_ids_for_request",
                new=AsyncMock(return_value={}),
            ),
            pytest.raises(ExtendedHTTPException) as raised,
        ):
            await proxy_router._apply_budget_attribution(user, request_info)

        assert raised.value.code == 503
        assert probes.await_count == 1
