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

"""Getting the spend metadata onto an outbound LiteLLM call, per call family.

Three transports with different constraints: chat fixes its headers at construction yet needs a
per-call run id; embeddings build a model per operation so both ids can be fixed; the proxy builds
the request itself and must first STRIP whatever the client sent.
"""

import json
from contextlib import ExitStack, asynccontextmanager, nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from codemie.enterprise.litellm.runtime_budget_selection import RuntimeBudgetMode
from codemie.repository.spend_logs_model import LiteLLMSpendLog as Row
from codemie.repository.spend_logs_repository import SpendLogsRepository
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.ledger import PersonalHolder, ProjectHolder
from codemie.service.spend.origin_context import spend_origin
from codemie.service.spend.run_context import publish_run_scope, reset_run_scope
from codemie.service.spend.spend_logs_metadata import SPEND_LOGS_METADATA_HEADER
from codemie.service.spend.traffic_class import TrafficOrigin

USER_ID = "aaaaaaaa-1111-1111-1111-111111111111"


def metadata_in(headers: dict) -> dict:
    return json.loads(headers[SPEND_LOGS_METADATA_HEADER])


class TestTheProjectInContextIsNotAlwaysAProject:
    """`current_project` holds the user's own email for a non-member on a global assistant."""

    def test_a_real_project_is_reported(self):
        from codemie.enterprise.litellm.llm_factory import _project_in_context

        ctx = SimpleNamespace(current_project="acme", credentials=None, principal_email=None)
        assert _project_in_context(ctx, "alice@example.com") == "acme"

    def test_the_users_own_email_is_not_reported_as_a_project(self):
        """Sent as codemie_project it would attribute her spend to a 'project' named after her."""
        from codemie.enterprise.litellm.llm_factory import _project_in_context

        ctx = SimpleNamespace(current_project="alice@example.com", credentials=None, principal_email=None)
        assert _project_in_context(ctx, "alice@example.com") is None

    def test_the_personal_project_is_not_reported_when_the_username_is_not_the_email(self):
        """The customer key is the username; the personal project is named after the email.

        Where the identity provider keeps the two apart, comparing the project with the customer key
        alone let a chat in her own personal project leave as `project_scoped` to a "project" named
        after her, and out of her personal figure.
        """
        from codemie.enterprise.litellm.llm_factory import _project_in_context

        ctx = SimpleNamespace(
            current_project="alice@example.com", credentials=None, principal_email="alice@example.com"
        )
        assert _project_in_context(ctx, "alice") is None

    def test_a_real_project_is_reported_whoever_the_principal_is(self):
        from codemie.enterprise.litellm.llm_factory import _project_in_context

        ctx = SimpleNamespace(current_project="acme", credentials=None, principal_email="alice@example.com")
        assert _project_in_context(ctx, "alice") == "acme"

    @pytest.mark.parametrize(
        "ctx", [None, SimpleNamespace(current_project=None, credentials=None, principal_email=None)]
    )
    def test_no_context_is_no_project(self, ctx):
        from codemie.enterprise.litellm.llm_factory import _project_in_context

        assert _project_in_context(ctx, "alice@example.com") is None


class TestChatCarriesEverythingButTheRunIdAtConstruction:
    """One model object serves a whole agent loop, so a run id fixed here would name one call for all."""

    def test_the_construction_time_header_has_no_run_id(self):
        from codemie.enterprise.litellm.llm_factory import _spend_attribution_values
        from codemie.service.spend.spend_logs_metadata import spend_logs_metadata_header

        with patch("codemie.rest_api.security.workflow_context.get_current_workflow_id", return_value=None):
            values = _spend_attribution_values(
                request_id="req-1",
                user_id=USER_ID,
                category=BudgetCategory.PLATFORM,
                litellm_context=SimpleNamespace(current_project="acme", credentials=None, principal_email=None),
                username="alice@example.com",
            )
        payload = metadata_in(spend_logs_metadata_header(**values))
        assert "codemie_run_id" not in payload
        assert payload["codemie_request_id"] == "req-1"
        assert payload["codemie_project"] == "acme"

    def test_a_personal_project_chat_leaves_in_principal_form(self):
        """The header boundary: no `codemie_project` when the project is her own email."""
        from codemie.enterprise.litellm.llm_factory import _spend_attribution_values
        from codemie.rest_api.models.settings import LiteLLMContext
        from codemie.service.spend.spend_logs_metadata import spend_logs_metadata_header

        with patch("codemie.rest_api.security.workflow_context.get_current_workflow_id", return_value=None):
            values = _spend_attribution_values(
                request_id="req-1",
                user_id=USER_ID,
                category=BudgetCategory.PLATFORM,
                litellm_context=LiteLLMContext(
                    credentials=None, current_project="alice@example.com", principal_email="alice@example.com"
                ),
                username="alice",
            )
        payload = metadata_in(spend_logs_metadata_header(**values))
        assert "codemie_project" not in payload
        assert payload["codemie_user_id"] == USER_ID

    def test_the_workflow_id_comes_from_the_workflow_context_not_the_litellm_context(self):
        """By model-construction time LiteLLMContext holds the NODE'S ASSISTANT, not the workflow.

        A value taken from there is wrong and self-consistent: no NULL, no defect count, nothing to
        notice.
        """
        from codemie.enterprise.litellm import llm_factory

        with patch.object(llm_factory, "_project_in_context", return_value=None):
            with patch("codemie.rest_api.security.workflow_context.get_current_workflow_id", return_value="wf-7"):
                values = llm_factory._spend_attribution_values(
                    request_id="req-1",
                    user_id=USER_ID,
                    category=BudgetCategory.PLATFORM,
                    litellm_context=None,
                    username=None,
                )
        assert values["workflow_id"] == "wf-7"

    def test_no_resolved_category_sends_no_identity(self):
        """The own-credentials bypass resolves no category, and a guessed one is worse than none."""
        from codemie.enterprise.litellm.llm_factory import _spend_attribution_values
        from codemie.service.spend.spend_logs_metadata import spend_logs_metadata_header

        with patch("codemie.rest_api.security.workflow_context.get_current_workflow_id", return_value=None):
            values = _spend_attribution_values(
                request_id="req-1",
                user_id=USER_ID,
                category=None,
                litellm_context=None,
                username=None,
            )
        payload = metadata_in(spend_logs_metadata_header(**values))
        assert payload == {"codemie_request_id": "req-1"}


class TestEachChatCallStampsItsOwnRunId:
    def test_the_per_call_header_adds_the_run_id_in_flight(self):
        from codemie.enterprise.litellm.llm_factory import LiteLLMChatOpenAI

        model = object.__new__(LiteLLMChatOpenAI)
        object.__setattr__(
            model,
            "spend_attribution",
            {
                "request_id": "req-1",
                "user_id": USER_ID,
                "category": "platform",
                "project": "acme",
                "workflow_id": None,
            },
        )
        with patch.object(LiteLLMChatOpenAI.__mro__[1], "_get_request_payload", return_value={"messages": []}):
            token = publish_run_scope(request_id="req-1", run_id="run-42")
            try:
                payload = model._get_request_payload([])
            finally:
                reset_run_scope(token)

        assert metadata_in(payload["extra_headers"])["codemie_run_id"] == "run-42"

    def test_a_run_id_published_by_another_request_is_not_borrowed(self):
        from codemie.enterprise.litellm.llm_factory import LiteLLMChatOpenAI

        model = object.__new__(LiteLLMChatOpenAI)
        object.__setattr__(model, "spend_attribution", {"request_id": "req-1", "user_id": USER_ID})
        with patch.object(LiteLLMChatOpenAI.__mro__[1], "_get_request_payload", return_value={"messages": []}):
            token = publish_run_scope(request_id="req-OTHER", run_id="run-42")
            try:
                payload = model._get_request_payload([])
            finally:
                reset_run_scope(token)

        assert "codemie_run_id" not in metadata_in(payload["extra_headers"])

    def test_a_model_with_no_attribution_adds_no_header(self):
        from codemie.enterprise.litellm.llm_factory import LiteLLMChatOpenAI

        model = object.__new__(LiteLLMChatOpenAI)
        object.__setattr__(model, "spend_attribution", {})
        with patch.object(LiteLLMChatOpenAI.__mro__[1], "_get_request_payload", return_value={"messages": []}):
            assert model._get_request_payload([]) == {"messages": []}


class TestTheProxyStripsWhatTheClientSentAndSetsItsOwn:
    """RELEASE-BLOCKING. This header carries identity, so a client able to set it could book its
    spend against ANOTHER PROJECT'S BUDGET - both readings well-formed, nothing failing."""

    def test_the_header_is_in_the_not_forwarded_set(self):
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")
        assert SPEND_LOGS_METADATA_HEADER.lower() in proxy_router.PROXY_HOP_BY_HOP_HEADERS

    def test_a_client_supplied_value_never_reaches_the_upstream_request(self):
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        forged = json.dumps({"codemie_project": "someone-elses-project", "codemie_user_id": USER_ID})
        request = MagicMock()
        request.headers = {SPEND_LOGS_METADATA_HEADER: forged, "content-type": "application/json"}
        user = SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com")
        request_info = {
            proxy_router.REQUEST_ID: "req-1",
            proxy_router.PROJECT: "acme",
            proxy_router.SPEND_ATTRIBUTION_PROJECT: "acme",
            proxy_router.SPEND_ATTRIBUTION_CATEGORY: BudgetCategory.CLI.value,
        }

        with patch.object(proxy_router, "_apply_context_headers"):
            headers = proxy_router._prepare_proxy_headers(request, request_info, user=user)

        assert not isinstance(headers, Exception)
        assert headers[SPEND_LOGS_METADATA_HEADER] != forged
        assert metadata_in(headers)["codemie_project"] == "acme"
        assert "someone-elses-project" not in headers[SPEND_LOGS_METADATA_HEADER]

    def test_the_dimension_key_is_omitted_rather_than_invented(self):
        """Nothing on this path knows a workflow, so the key is absent by design."""
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        headers: dict = {}
        proxy_router._apply_spend_logs_metadata_header(
            headers,
            {
                proxy_router.REQUEST_ID: "req-1",
                proxy_router.PROJECT: "acme",
                proxy_router.SPEND_ATTRIBUTION_CATEGORY: BudgetCategory.CLI.value,
            },
            SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com"),
        )
        payload = metadata_in(headers)
        assert "codemie_workflow_id" not in payload
        assert payload["codemie_category"] == "cli"

    def test_a_run_id_is_always_minted_so_retried_attempts_count_once(self):
        """Without one, every attempt row carries a NULL run id and each counts as its own request."""
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        headers: dict = {}
        proxy_router._apply_spend_logs_metadata_header(
            headers,
            {proxy_router.REQUEST_ID: "req-1", proxy_router.SPEND_ATTRIBUTION_CATEGORY: "cli"},
            SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com"),
        )
        assert metadata_in(headers)["codemie_run_id"]

    def test_a_request_no_routing_categorised_sends_no_identity(self):
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        headers: dict = {}
        proxy_router._apply_spend_logs_metadata_header(
            headers,
            {proxy_router.REQUEST_ID: "req-1"},  # no category recorded: no budget routing ran
            SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com"),
        )
        payload = metadata_in(headers)
        assert "codemie_user_id" not in payload
        assert payload["codemie_request_id"] == "req-1"

    def test_the_callers_username_is_not_reported_as_a_project(self):
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        headers: dict = {}
        proxy_router._apply_spend_logs_metadata_header(
            headers,
            {
                proxy_router.REQUEST_ID: "req-1",
                proxy_router.PROJECT: "alice",  # the fallback when no project header was sent
                proxy_router.SPEND_ATTRIBUTION_PROJECT: "alice",
                proxy_router.SPEND_ATTRIBUTION_CATEGORY: "cli",
            },
            SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com"),
        )
        assert "codemie_project" not in metadata_in(headers)

    def test_the_callers_personal_project_is_not_reported_when_the_username_is_not_the_email(self):
        """The personal project is named after the email, which the CLI sends as its project header."""
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        headers: dict = {}
        proxy_router._apply_spend_logs_metadata_header(
            headers,
            {
                proxy_router.REQUEST_ID: "req-1",
                proxy_router.PROJECT: "alice@example.com",
                proxy_router.SPEND_ATTRIBUTION_PROJECT: "alice@example.com",
                proxy_router.SPEND_ATTRIBUTION_CATEGORY: "cli",
            },
            SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com"),
        )
        assert "codemie_project" not in metadata_in(headers)


class TestTheOwnCredentialsBypassStillSendsIdentity:
    """Enforcement is bypassed on this path; attribution is not.

    Leaving identity off would leave `attr_user_id` NULL forever, making the project-member holder
    structurally blind to this traffic and leaving every other holder dependent on the
    username/email alias set - which exists as insurance, not as a path to rely on.
    """

    def test_the_direct_path_classifies_by_traffic_kind_not_by_budget(self):
        from codemie.enterprise.litellm import llm_factory

        model_details = SimpleNamespace(base_name="gpt-4.1", configuration=None)
        creds = SimpleNamespace(api_key="the-users-own-key")

        with patch.object(llm_factory, "_classify_direct_traffic", wraps=llm_factory._classify_direct_traffic):
            with patch("codemie.enterprise.litellm.dependencies.get_premium_username", return_value=None):
                category = llm_factory._configure_direct_runtime_overrides(
                    llm_model_details=model_details,
                    litellm_context=SimpleNamespace(current_project=None, credentials=creds, principal_email=None),
                    username="alice@example.com",
                    user_id=USER_ID,
                    creds=creds,
                    merged_headers={},
                    request_params={},
                )

        assert category is BudgetCategory.PLATFORM, "a bypass call must still resolve a category"

    def test_a_premium_model_on_the_direct_bypass_is_classified_premium(self):
        from codemie.enterprise.litellm import llm_factory

        model_details = SimpleNamespace(base_name="claude-opus-4-6-20260205", configuration=None)
        with patch("codemie.enterprise.litellm.dependencies.get_premium_username", return_value="alice_premium"):
            assert (
                llm_factory._classify_direct_traffic(model_details, "alice@example.com")
                is BudgetCategory.PREMIUM_MODELS
            )

    def test_a_cli_bypass_call_is_classified_cli_and_not_platform(self):
        """`attr_category = 'cli'` is the native CLI discriminator.

        Asking the budget-selecting resolvers here would answer `platform`, because no budget is
        available to select on this path - and the CLI bucket would quietly empty for every bypass
        user, which is indistinguishable from no CLI spend.
        """
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        # `_is_cli_request`: a non-empty X-CodeMie-CLI header OR `client_type` codemie-cli/codemie_cli, so the
        # `client_type` alone suffices here. Extension traffic is non-CLI only because the extension stopped
        # sending X-CodeMie-CLI (EPMCDME-14260).
        request_info = {proxy_router.CLIENT_TYPE: "codemie-cli", proxy_router.LLM_MODEL: "gpt-4.1"}
        with patch.object(proxy_router, "get_premium_username", return_value=None):
            category = proxy_router._classify_proxy_traffic(
                SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com"), request_info
            )
        # Compared by VALUE, not identity: the proxy router speaks the LiteLLM BudgetCategory and the
        # header builder validates against the core one. They are distinct classes, and the string is
        # what actually travels between them.
        assert category.value == BudgetCategory.CLI.value

    def test_a_non_cli_non_premium_proxy_bypass_is_platform(self):
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        with patch.object(proxy_router, "get_premium_username", return_value=None):
            category = proxy_router._classify_proxy_traffic(
                SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com"),
                {proxy_router.LLM_MODEL: "gpt-4.1"},
            )
        assert category.value == BudgetCategory.PLATFORM.value

    @pytest.mark.asyncio
    async def test_the_proxy_bypass_records_the_category_for_the_header_builder(self):
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        request_info = {
            proxy_router.REQUEST_ID: "req-1",
            proxy_router.LLM_MODEL: "gpt-4.1",
            proxy_router.CLIENT_TYPE: "codemie-cli",
        }
        user_credentials = SimpleNamespace(is_personal=True, alias="my-key")

        with patch.object(proxy_router, "get_premium_username", return_value=None):
            with patch.object(proxy_router, "_resolve_bypass_mode_stream", return_value="stream"):
                await proxy_router._resolve_budget_attribution_stream(
                    body_bytes=b"{}",
                    user=SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com"),
                    request_info=request_info,
                    user_credentials=user_credentials,
                )

        assert request_info[proxy_router.SPEND_ATTRIBUTION_CATEGORY] == "cli"

        headers: dict = {}
        proxy_router._apply_spend_logs_metadata_header(
            headers, request_info, SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com")
        )
        payload = metadata_in(headers)
        assert payload["codemie_user_id"] == USER_ID
        assert payload["codemie_category"] == "cli"

    async def _bypass(self, project: str) -> tuple[dict, dict]:
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        user = SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com")
        request_info = {
            proxy_router.REQUEST_ID: "req-1",
            proxy_router.LLM_MODEL: "gpt-4.1",
            proxy_router.CLIENT_TYPE: "codemie-cli",
            proxy_router.PROJECT: project,
        }
        with patch.object(proxy_router, "get_premium_username", return_value=None):
            with patch.object(proxy_router, "_resolve_bypass_mode_stream", return_value="stream"):
                await proxy_router._resolve_budget_attribution_stream(
                    body_bytes=b"{}",
                    user=user,
                    request_info=request_info,
                    user_credentials=SimpleNamespace(is_personal=True, alias="my-key"),
                )
        headers: dict = {}
        proxy_router._apply_spend_logs_metadata_header(headers, request_info, user)
        return request_info, metadata_in(headers)

    @pytest.mark.asyncio
    async def test_a_bypass_call_naming_a_project_still_names_it(self):
        """Own-key calls keep their project until their own follow-up decides otherwise."""
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        request_info, payload = await self._bypass("project-a")
        assert request_info[proxy_router.SPEND_ATTRIBUTION_PROJECT] == "project-a"
        assert payload["codemie_project"] == "project-a"

    @pytest.mark.asyncio
    async def test_a_bypass_call_in_the_callers_personal_project_names_no_project(self):
        _, payload = await self._bypass("alice@example.com")
        assert "codemie_project" not in payload
        assert payload["codemie_user_id"] == USER_ID


IN_PERIOD = datetime(2026, 8, 10, tzinfo=timezone.utc)
LEDGER_PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))
DISPATCH_RETURNS_NONE = object()
ALICE_ALIASES = frozenset({"alice", "alice@example.com"})
PROXY_ROUTER = "codemie.enterprise.litellm.proxy_router"


def _ledger_row(payload: dict, end_user: str) -> Row:
    """The row the uploader stores for a complete identity triple (codemie-enterprise `attribution.decide`)."""
    project = payload.get("codemie_project")
    return Row(
        request_id="req-1",
        start_time=IN_PERIOD,
        end_time=IN_PERIOD,
        spend=Decimal("1.25"),
        status="success",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        attr_source="explicit",
        attr_form="project_scoped" if project else "principal",
        attr_project=project,
        attr_category=payload["codemie_category"],
        attr_user_id=payload["codemie_user_id"],
        end_user=end_user,
    )


class _SyncSessionAsAsync:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)


@asynccontextmanager
async def _no_session():
    yield MagicMock()


def _routing_cases():
    from codemie.enterprise.litellm.budget_categories import BudgetCategory as ProxyBudgetCategory

    funded_runtime = SimpleNamespace(
        headers={"x-litellm-customer-id": f"codemie:project:project-a:category:cli:user:{USER_ID}"}
    )
    cli = {ProxyBudgetCategory.CLI}
    return [
        pytest.param("codemie-cli", cli, funded_runtime, "project-a", "project", id="funded_member"),
        pytest.param("codemie-cli", set(), None, None, "personal", id="admin_non_member"),
        pytest.param(None, cli, None, None, "personal", id="member_other_category"),
        pytest.param("codemie-cli", cli, DISPATCH_RETURNS_NONE, None, "personal", id="allocation_dispatch_none"),
    ]


ROUTING_CASE_FIELDS = "client_type, probe_scopes, runtime, expected_project, expected_budget"


class _Dispatched:
    """A project runtime produced by the real resolver from this dispatch result."""

    def __init__(self, result) -> None:
        self.result = result


async def _route_proxy_call(
    client_type,
    probe_scopes,
    runtime,
    *,
    llm_model: str = "gpt-4.1",
    premium_username: str | None = None,
    user_credentials=None,
) -> tuple[dict, MagicMock]:
    import importlib

    proxy_router = importlib.import_module(PROXY_ROUTER)

    user = MagicMock(id=USER_ID, username="alice", email="alice@example.com")
    request_info = {
        proxy_router.REQUEST_ID: "req-1",
        proxy_router.LLM_MODEL: llm_model,
        proxy_router.PROJECT: "project-a",
    }
    if client_type:
        request_info[proxy_router.CLIENT_TYPE] = client_type

    budget_ids = {"platform": None, "cli": "cli-budget", "premium_models": None}
    member_tracking = SimpleNamespace(mode=RuntimeBudgetMode.PROJECT_BUDGET_WITH_MEMBER_TRACKING)
    patches = [
        patch(f"{PROXY_ROUTER}.get_premium_username", return_value=premium_username),
        patch(
            f"{PROXY_ROUTER}.budget_service.get_all_category_budget_ids_for_request",
            new=AsyncMock(return_value=budget_ids),
        ),
        patch(f"{PROXY_ROUTER}._probe_project_budget_scopes", new=AsyncMock(return_value=probe_scopes)),
        patch(f"{PROXY_ROUTER}.select_runtime_budget_mode", return_value=member_tracking),
        patch(f"{PROXY_ROUTER}.check_user_budget", return_value=MagicMock()),
        patch(f"{PROXY_ROUTER}.resolve_mirrored_budget_id", return_value=None),
        patch(f"{PROXY_ROUTER}.budget_service.track_proxy_budget_assignment_for_request", new=AsyncMock()),
    ]
    if runtime is DISPATCH_RETURNS_NONE or isinstance(runtime, _Dispatched):
        dispatched = None if runtime is DISPATCH_RETURNS_NONE else runtime.result
        patches += [
            patch(f"{PROXY_ROUTER}.ensure_project_member_runtime_ready", new=AsyncMock()),
            patch(f"{PROXY_ROUTER}.get_async_session", new=_no_session),
            patch(f"{PROXY_ROUTER}.budget_resolution_service.resolve", new=AsyncMock(return_value=MagicMock())),
            patch(
                f"{PROXY_ROUTER}.budget_resolution_service.dispatch_runtime",
                new=AsyncMock(return_value=dispatched),
            ),
        ]
    else:
        patches.append(patch(f"{PROXY_ROUTER}._resolve_project_budget_runtime", new=AsyncMock(return_value=runtime)))

    with ExitStack() as stack:
        for active in patches:
            stack.enter_context(active)
        await proxy_router._resolve_budget_attribution_stream(
            body_bytes=b"{}", user=user, request_info=request_info, user_credentials=user_credentials
        )
    return request_info, user


class TestTheProxyNamesOnlyTheProjectItCharged:
    """A personal-budget fallback was charged to the person, so naming the project in its header
    filed the same call under the project as well - one call, two budgets.

    `member_other_category` is a member with only a CLI allocation making a platform call: routing
    picks `platform`, and the project has no runtime for it.
    """

    async def _route(self, client_type, probe_scopes, runtime) -> tuple[dict, MagicMock]:
        return await _route_proxy_call(client_type, probe_scopes, runtime)

    async def _routed_payload(self, client_type, probe_scopes, runtime) -> tuple[dict, dict]:
        import importlib

        proxy_router = importlib.import_module(PROXY_ROUTER)

        request_info, user = await self._route(client_type, probe_scopes, runtime)
        headers: dict = {}
        proxy_router._apply_spend_logs_metadata_header(headers, request_info, user)
        return request_info, metadata_in(headers)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(ROUTING_CASE_FIELDS, _routing_cases())
    async def test_routing_records_the_project_it_charged(
        self, client_type, probe_scopes, runtime, expected_project, expected_budget
    ):
        import importlib

        proxy_router = importlib.import_module(PROXY_ROUTER)

        request_info, _ = await self._route(client_type, probe_scopes, runtime)
        assert request_info[proxy_router.SPEND_ATTRIBUTION_PROJECT] == expected_project
        assert request_info[proxy_router.PROJECT] == "project-a"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(ROUTING_CASE_FIELDS, _routing_cases())
    async def test_a_fallback_header_keeps_identity_but_names_no_project(
        self, client_type, probe_scopes, runtime, expected_project, expected_budget
    ):
        import importlib

        proxy_router = importlib.import_module(PROXY_ROUTER)

        request_info, payload = await self._routed_payload(client_type, probe_scopes, runtime)
        assert payload.get("codemie_project") == expected_project
        assert payload["codemie_user_id"] == USER_ID
        assert payload["codemie_category"] == request_info[proxy_router.SPEND_ATTRIBUTION_CATEGORY]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(ROUTING_CASE_FIELDS, _routing_cases())
    async def test_the_row_lands_in_exactly_one_budget(
        self, client_type, probe_scopes, runtime, expected_project, expected_budget
    ):
        request_info, payload = await self._routed_payload(client_type, probe_scopes, runtime)
        category = BudgetCategory(payload["codemie_category"])
        holders = {
            "project": ProjectHolder("project-a", category),
            "personal": PersonalHolder(USER_ID, category, ALICE_ALIASES),
        }

        engine = create_engine("sqlite://")
        Row.metadata.create_all(engine, tables=[Row.__table__])
        with Session(engine) as sync_session:
            sync_session.add(_ledger_row(payload, end_user=request_info["litellm_customer_id"]))
            sync_session.commit()
            session = _SyncSessionAsAsync(sync_session)
            sums = {
                name: await SpendLogsRepository().sum_for_holder(session, holder, LEDGER_PERIOD, None)
                for name, holder in holders.items()
            }

        assert {name for name, total in sums.items() if total} == {expected_budget}


APP_KEY = "sk-app"
PROJECT_PREMIUM_KEY = "sk-project-premium"
BUDGET_PROVIDER_KEY = "sk-budget-provider"
OWN_KEY = "sk-alice"


def _credentials(api_key: str, *, is_personal: bool):
    return SimpleNamespace(
        credentials=SimpleNamespace(api_key=api_key),
        alias="codemie:project:project-a:category:premium_models" if not is_personal else "alice-key",
        is_personal=is_personal,
        setting_id="setting-1",
    )


def _premium_auth_cases():
    from codemie.enterprise.litellm.budget_categories import BudgetCategory as ProxyBudgetCategory

    funded = _Dispatched(
        SimpleNamespace(
            provider="litellm",
            headers={"x-litellm-customer-id": f"codemie:project:project-a:category:premium_models:user:{USER_ID}"},
            api_key=BUDGET_PROVIDER_KEY,
            base_url=None,
        )
    )
    project_key = _credentials(PROJECT_PREMIUM_KEY, is_personal=False)
    return [
        pytest.param(project_key, set(), None, APP_KEY, None, id="personal_fallback_with_project_premium_key"),
        pytest.param(
            project_key,
            {ProxyBudgetCategory.PREMIUM_MODELS},
            funded,
            BUDGET_PROVIDER_KEY,
            "project-a",
            id="funded_premium_member",
        ),
        pytest.param(
            _credentials(OWN_KEY, is_personal=True), set(), None, OWN_KEY, "project-a", id="own_credentials_bypass"
        ),
    ]


class TestAProjectPremiumKeyNeverAuthenticatesAPersonalFallback:
    """A call that fell back to the personal budget names no project, so the project's premium key
    must not pay for it either: LiteLLM would charge a project whose ledger never sees the row.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "user_credentials, probe_scopes, runtime, expected_key, expected_project", _premium_auth_cases()
    )
    async def test_the_key_that_pays_matches_the_project_the_row_names(
        self, user_credentials, probe_scopes, runtime, expected_key, expected_project
    ):
        import importlib

        proxy_router = importlib.import_module(PROXY_ROUTER)

        request_info, user = await _route_proxy_call(
            "codemie-cli",
            probe_scopes,
            runtime,
            llm_model="claude-opus-4-6-20260205",
            premium_username="alice_premium",
            user_credentials=user_credentials,
        )
        request = MagicMock()
        request.headers = {"content-type": "application/json"}
        with (
            patch.object(proxy_router, "_apply_context_headers"),
            patch.object(proxy_router.config, "LITE_LLM_PROXY_APP_KEY", APP_KEY),
        ):
            headers = proxy_router._prepare_proxy_headers(request, request_info, user_credentials, user)

        assert headers["Authorization"] == f"Bearer {expected_key}"
        assert metadata_in(headers).get("codemie_project") == expected_project


class TestTheTwoBudgetCategoryEnumsMustNotDrift:
    def test_they_agree_on_every_value(self):
        """A latent coupling, pinned because drifting it fails **silently**.

        The proxy router classifies with the LiteLLM enum and the header builder validates the
        category against the core one, so only the string joins them. If either enum renamed or
        dropped a member, the builder would reject the identity triple as a whole - precondition B
        is all-or-nothing - and every affected row would fall to the fallback parser with no error
        anywhere and `attr_user_id` NULL.
        """
        from codemie.enterprise.litellm.budget_categories import BudgetCategory as LiteLLMBudgetCategory

        assert sorted(c.value for c in LiteLLMBudgetCategory) == sorted(c.value for c in BudgetCategory)


class TestTheOriginIsReadAtConstructionAndAgainPerCall:
    """Two reads, because a chat model is built on one thread and invoked on others.

    Neither read alone covers both real cases: the construction-time one misses a nested block that
    published a narrower origin, and the call-time one misses a pool thread that copied no context.
    """

    def _attribution(self, origin: str | None):
        with patch("codemie.rest_api.security.workflow_context.get_current_workflow_id", return_value=None):
            with spend_origin(TrafficOrigin(origin)) if origin else nullcontext():
                from codemie.enterprise.litellm.llm_factory import _spend_attribution_values

                return _spend_attribution_values(
                    request_id="req-1",
                    user_id=USER_ID,
                    category=BudgetCategory.PLATFORM,
                    litellm_context=None,
                    username=None,
                )

    def _payload_of_a_call(self, attribution: dict, call_time_origin: str | None):
        from codemie.enterprise.litellm.llm_factory import LiteLLMChatOpenAI

        model = object.__new__(LiteLLMChatOpenAI)
        object.__setattr__(model, "spend_attribution", attribution)
        with patch.object(LiteLLMChatOpenAI.__mro__[1], "_get_request_payload", return_value={"messages": []}):
            with spend_origin(TrafficOrigin(call_time_origin)) if call_time_origin else nullcontext():
                payload = model._get_request_payload([])
        return metadata_in(payload["extra_headers"])

    def test_the_construction_time_header_carries_what_the_entry_point_published(self):
        from codemie.service.spend.spend_logs_metadata import spend_logs_metadata_header

        values = self._attribution("conversation")
        assert metadata_in(spend_logs_metadata_header(**values))["codemie_origin"] == "conversation"

    def test_nothing_published_at_construction_sends_no_origin_key(self):
        """A pooled thread that copied no context is a KNOWN hole, never a guessed value."""
        from codemie.service.spend.spend_logs_metadata import spend_logs_metadata_header

        values = self._attribution(None)
        assert values["origin"] is None
        assert "codemie_origin" not in metadata_in(spend_logs_metadata_header(**values))

    def test_the_call_time_origin_wins_over_the_construction_time_one(self):
        """A tool running its own model inside a conversation is tool traffic for that call."""
        payload = self._payload_of_a_call(self._attribution("conversation"), "tools")
        assert payload["codemie_origin"] == "tools"

    def test_the_construction_time_origin_stands_when_the_calling_thread_published_nothing(self):
        payload = self._payload_of_a_call(self._attribution("conversation"), None)
        assert payload["codemie_origin"] == "conversation"

    def test_a_call_time_origin_is_used_when_construction_knew_none(self):
        payload = self._payload_of_a_call(self._attribution(None), "workflow")
        assert payload["codemie_origin"] == "workflow"

    def test_no_origin_anywhere_leaves_every_other_key_as_it_was(self):
        payload = self._payload_of_a_call(self._attribution(None), None)
        assert "codemie_origin" not in payload
        assert payload["codemie_request_id"] == "req-1"
        assert payload["codemie_user_id"] == USER_ID
        assert payload["codemie_category"] == "platform"


class TestTheEmbeddingsHeaderCarriesTheOriginToo:
    """One model object per embedding operation, so the construction-time read is the only read."""

    def test_the_published_origin_reaches_the_fixed_headers(self):
        from codemie.configs.config import config
        from codemie.enterprise.litellm import llm_factory

        model_details = SimpleNamespace(base_name="text-embedding-ada-002", configuration=None)
        with (
            patch.object(config, "LITE_LLM_URL", "http://test:4000"),
            patch.object(config, "LITE_LLM_APP_KEY", "test-key"),
            patch.object(config, "OPENAI_API_TYPE", "azure"),
            patch.object(config, "OPENAI_API_VERSION", "2025-04-01-preview"),
            patch.object(llm_factory, "LiteLLMAzureOpenAIEmbeddings") as embeddings,
            patch("codemie.enterprise.litellm.dependencies.check_user_budget"),
        ):
            with spend_origin(TrafficOrigin.DATASOURCE_INDEXING):
                llm_factory.create_litellm_embedding_model(
                    embedding_model="text-embedding-ada-002",
                    llm_model_details=model_details,
                    litellm_context=None,
                    username="alice@example.com",
                    user_id=USER_ID,
                )

        headers = embeddings.call_args.kwargs["default_headers"]
        assert metadata_in(headers)["codemie_origin"] == "datasource_indexing"


class TestTheSttClientCarriesIdentityAndOriginToo:
    """One client per transcribe call (SttService._build_client), so a run id minted here is enough."""

    def test_the_identity_triple_and_published_origin_reach_the_default_headers(self):
        from codemie.configs.config import config
        from codemie.configs.llm_config import LLMModel
        from codemie.enterprise.litellm.stt_client import get_litellm_stt_client
        from codemie.core.constants import LLMProxyMode

        model = LLMModel(base_name="custom-stt", deployment_name="custom-stt", enabled=True)
        with (
            patch.object(config, "LLM_PROXY_MODE", LLMProxyMode.lite_llm),
            patch.object(config, "LITE_LLM_URL", "http://test:4000"),
            patch.object(config, "LITE_LLM_APP_KEY", "test-key"),
            patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True),
            patch("codemie.enterprise.litellm.dependencies.check_user_budget"),
        ):
            with spend_origin(TrafficOrigin.CLI):
                client = get_litellm_stt_client(model, None, "alice", USER_ID)

        payload = metadata_in(client.default_headers)
        assert payload["codemie_user_id"] == USER_ID
        assert payload["codemie_category"] == "platform"
        assert payload["codemie_origin"] == "cli"


class TestTheProxyNamesTheClientTypeRegardlessOfWhoPaid:
    """The category says which budget paid; the origin says what the request was for.

    They are allowed to disagree, and CLI is the case where they routinely do: until this key
    existed, every CLI call a premium or platform budget paid for was lost to the CLI split.
    """

    def _proxy_payload(self, request_info: dict) -> dict:
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        headers: dict = {}
        proxy_router._apply_spend_logs_metadata_header(
            headers, request_info, SimpleNamespace(id=USER_ID, username="alice", email="alice@example.com")
        )
        return metadata_in(headers)

    def test_a_cli_session_billed_to_a_premium_budget_is_still_cli_traffic(self):
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        payload = self._proxy_payload(
            {
                proxy_router.REQUEST_ID: "req-1",
                proxy_router.CLIENT_TYPE: "codemie-cli",
                proxy_router.SPEND_ATTRIBUTION_CATEGORY: BudgetCategory.PREMIUM_MODELS.value,
            }
        )
        assert payload["codemie_origin"] == "cli"
        assert payload["codemie_category"] == "premium_models"

    def test_every_other_proxied_call_is_named_rather_than_left_null(self):
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        payload = self._proxy_payload(
            {
                proxy_router.REQUEST_ID: "req-1",
                proxy_router.SPEND_ATTRIBUTION_CATEGORY: BudgetCategory.PLATFORM.value,
            }
        )
        assert payload["codemie_origin"] == "proxy"

    def test_the_own_credentials_bypass_still_names_the_origin(self):
        """The origin is outside the identity triple, so a missing category cannot silence it."""
        import importlib

        proxy_router = importlib.import_module("codemie.enterprise.litellm.proxy_router")

        payload = self._proxy_payload({proxy_router.REQUEST_ID: "req-1", proxy_router.CLIENT_TYPE: "codemie-cli"})
        assert payload["codemie_origin"] == "cli"
        assert "codemie_user_id" not in payload
