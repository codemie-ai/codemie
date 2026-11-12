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

"""EPMCDME-16625 matrix rows that span several modules: web context, CLI identity, IDP mode, triggers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.models.settings import LiteLLMCredentials
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.llm_service.utils import set_llm_context

_UTILS = "codemie.service.llm_service.utils"


def _asset(project: str, is_global: bool = False):
    asset = MagicMock(spec=["project", "is_global", "shared", "llm_model_type", "id"])
    asset.project = project
    asset.is_global = is_global
    asset.shared = False
    asset.llm_model_type = None
    return asset


def _context(asset, user, *, funded: set[str], creds=None, setting_type="user"):
    """Run set_llm_context with the real funded probe over a fixed set of funded projects."""

    def probe(project, _user_id):
        return {BudgetCategory.PLATFORM} if project in funded else set()

    setting = MagicMock()
    setting.setting_type = setting_type
    setting.is_global = True
    with (
        patch(f"{_UTILS}.set_litellm_context") as set_ctx,
        patch(f"{_UTILS}.set_dial_credentials"),
        patch(f"{_UTILS}.SettingsService") as settings,
        patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True),
        patch(f"{_UTILS}.config.LLM_PROXY_MODE", "lite_llm"),
        patch("codemie.enterprise.litellm.llm_factory._probe_direct_project_budget_scopes", side_effect=probe),
        patch("codemie.enterprise.litellm.dependencies.check_user_budget") as check_budget,
    ):
        settings.get_litellm_creds.return_value = creds
        settings.retrieve_setting.return_value = setting
        set_llm_context(asset, None, user)
    return set_ctx.call_args[0][0], check_budget


def _user(**overrides) -> User:
    fields = {
        "id": "u-1",
        "username": "alice",
        "email": "alice@example.com",
        "project_names": ["team-a", "team-d", "alice@example.com"],
        "default_project": "team-d",
    }
    fields.update(overrides)
    return User(**fields)


def test_row_19_exhausted_budget_is_not_rerouted():
    """Attribution never looks at spend: a funded project stays the charge, LiteLLM blocks or warns on it."""
    ctx, check_budget = _context(_asset("team-a"), _user(), funded={"team-a", "team-d"})

    assert ctx.current_project == "team-a"
    assert ctx.budget_fallback_kind is None
    check_budget.assert_not_called()


def test_row_18_personal_key_pays_and_attribution_does_not_move_it():
    creds = LiteLLMCredentials(api_key="own-key", url="https://litellm.test")

    ctx, _ = _context(_asset("team-a", is_global=True), _user(), funded=set(), creds=creds)

    assert ctx.credentials == creds
    assert ctx.budget_fallback_kind is None


def test_web_and_cli_use_the_same_personal_customer_id():
    """D9: a personal fallback from the web and from the CLI lands on the same LiteLLM customer."""
    from codemie.core.dependecies import _resolve_username
    from codemie.enterprise.litellm.proxy_router import BudgetAvailability, _resolve_tracking_identity

    user = _user()
    with patch("codemie.rest_api.security.user_context.get_current_user", return_value=user):
        web_customer = _resolve_username(user.email)
    with patch("codemie.enterprise.litellm.proxy_router.get_premium_username", return_value=None):
        _category, cli_customer, _budget_id, _model = _resolve_tracking_identity(
            user=user,
            request_info={"llm_model": "gpt-4.1", "client_type": "web", "codemie_cli": ""},
            availability=BudgetAvailability(user_budget_ids={}),
        )

    assert web_customer == cli_customer == "alice"


@pytest.mark.asyncio
async def test_idp_claims_mode_default_is_the_personal_project():
    """D8: without User Management the derived default is the personal project, as for a user with none assigned."""
    from codemie.rest_api.security.user_providers.legacy_jwt import LegacyJwtUserProvider

    claims_user = User(id="u-1", username="alice", email="alice@example.com", project_names=["zeta", "alice"])
    idp = MagicMock()
    idp.authenticate = AsyncMock(return_value=claims_user)
    with (
        patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", False),
        patch(
            "codemie.rest_api.security.user_providers.legacy_jwt.authentication_service.ensure_project_exists",
            new=AsyncMock(),
        ),
        patch(
            "codemie.rest_api.security.user_providers.legacy_jwt.idp_user_snapshot_service.remember",
            new=AsyncMock(),
        ),
    ):
        user = await LegacyJwtUserProvider().authenticate_and_load_user(MagicMock(), idp)
        ctx, _ = _context(None, user, funded={"zeta"})

    # The derived default lives only in attribution: credential lookup still sees no stored default.
    assert user.default_project is None
    assert ctx.current_project == "alice@example.com"
    assert ctx.budget_fallback_from is None


def test_row_17_trigger_owner_is_attributed_with_real_memberships():
    """D7: a scheduled run charges the owner's default project when the asset project cannot pay."""
    from codemie.triggers.bindings.utils import resolve_trigger_user

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    rows = [
        SimpleNamespace(project_name="team-a", is_default=False, is_project_admin=False),
        SimpleNamespace(project_name="team-d", is_default=True, is_project_admin=False),
    ]
    with (
        patch("codemie.clients.postgres.get_session", return_value=session),
        patch("codemie.repository.user_repository.user_repository") as users,
        patch("codemie.repository.user_project_repository.user_project_repository") as projects,
        patch("codemie.repository.user_kb_repository.user_kb_repository"),
    ):
        users.get_active_by_id.return_value = SimpleNamespace(
            id="u-1",
            username="alice",
            name="Alice",
            email="alice@example.com",
            picture="",
            user_type="regular",
            is_admin=False,
            is_maintainer=False,
            is_auditor=False,
            project_limit=None,
        )
        projects.get_by_user_id.return_value = rows
        owner = resolve_trigger_user("u-1")

    ctx, _ = _context(_asset("team-a"), owner, funded={"team-d"})

    assert ctx.current_project == "team-d"
    assert ctx.budget_fallback_from == "team-a"
    assert ctx.budget_fallback_kind == "default_project"


def _index(project: str):
    index = MagicMock(spec=["project_name", "project_space_visible", "id", "llm_model_type"])
    index.project_name = project
    index.project_space_visible = True
    index.llm_model_type = None
    return index


def test_row_9_project_workflow_member_funded_charges_the_workflow_project():
    ctx, _ = _context(_asset("team-a"), _user(), funded={"team-a", "team-d"})

    assert ctx.current_project == "team-a"


def test_row_10_marketplace_workflow_charges_the_default_project():
    ctx, _ = _context(_asset("team-a", is_global=True), _user(), funded={"team-a", "team-d"})

    assert ctx.current_project == "team-d"


def test_row_13_indexing_by_a_non_member_never_charges_the_datasource_project():
    ctx, _ = _context(_index("foreign"), _user(), funded={"foreign", "team-d"})

    assert ctx.current_project == "team-d"


def test_row_11_saved_assistant_in_a_marketplace_workflow_is_attributed_as_itself():
    """CR-006: a global workflow does not turn a member's saved project assistant into a marketplace one."""
    from codemie.service.assistant_service import AssistantService

    saved = MagicMock()
    saved.is_global = False
    saved.project = "team-a"
    saved.model_copy = lambda update: MagicMock(project="team-a", **update)
    node = MagicMock()
    node.assistant_id = "saved-assistant"

    with (
        patch.object(AssistantService, "_load_and_configure_workflow_assistant", return_value=saved),
        patch("codemie.service.assistant_service.set_llm_context") as set_ctx,
        patch("codemie.service.assistant_service.set_disable_prompt_cache", side_effect=RuntimeError("stop")),
        pytest.raises(RuntimeError, match="stop"),
    ):
        AssistantService.build_agent_for_workflow(
            user_input="hi",
            user=_user(),
            request_uuid="r",
            workflow_assistant=node,
            is_global=True,
        )

    assert set_ctx.call_args.args[0].is_global is False


def test_row_11_runtime_context_keeps_the_saved_assistant():
    """A marketplace workflow runs a global copy of a saved assistant; every invocation still bills the saved one."""
    from codemie.agents.langgraph_agent import LangGraphAgent

    saved, global_copy = _asset("team-a"), _asset("team-a", is_global=True)
    agent = object.__new__(LangGraphAgent)
    agent.assistant = global_copy

    agent.billing_assistant = saved
    assert agent.llm_context_asset is saved

    agent.billing_assistant = None
    assert agent.llm_context_asset is global_copy


def test_row_11_workflow_agent_is_built_with_the_saved_assistant_for_billing():
    from contextlib import ExitStack

    from codemie.agents.langgraph_agent import LangGraphAgent
    from codemie.service.assistant_service import AssistantService

    saved = MagicMock()
    saved.is_global = False
    saved.project = "team-a"
    saved.toolkits = []
    saved.skill_ids = []
    global_copy = MagicMock()
    global_copy.is_global = True
    global_copy.toolkits = []
    global_copy.skill_ids = []
    saved.model_copy = lambda update: global_copy
    node = MagicMock()
    node.assistant_id = "saved-assistant"
    node.limit_tool_output_tokens = None

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(AssistantService, "_load_and_configure_workflow_assistant", return_value=saved)
        )
        stack.enter_context(patch.object(AssistantService, "_apply_marketplace_tool_mappings"))
        stack.enter_context(patch.object(AssistantService, "prepare_tools_config_from_toolkits", return_value=None))
        stack.enter_context(patch.object(AssistantService, "_prepare_workflow_system_prompt", return_value=("p", None)))
        stack.enter_context(
            patch.object(AssistantService, "_select_agent_class_for_workflow", return_value=LangGraphAgent)
        )
        stack.enter_context(patch.object(AssistantService, "_configure_langgraph_agent"))
        stack.enter_context(
            patch("codemie.service.assistant_service.LangGraphAssistantBuilder.resolve_tool_policy", return_value=None)
        )
        agent_class = stack.enter_context(patch("codemie.service.assistant_service.LangGraphAgent"))
        stack.enter_context(patch("codemie.service.assistant_service.set_llm_context"))
        stack.enter_context(patch("codemie.service.assistant_service.set_disable_prompt_cache"))
        stack.enter_context(patch("codemie.service.assistant_service.build_unique_file_objects_list", return_value={}))
        stack.enter_context(patch("codemie.service.assistant_service.prepare_skill_workspace", return_value=[]))
        stack.enter_context(patch("codemie.service.assistant_service.ToolkitService.get_tools", return_value=[]))
        stack.enter_context(patch("codemie.service.tools.oauth_connect_gate.enforce_oauth_connected"))
        stack.enter_context(patch("codemie.service.assistant_service.AssistantChatRequest"))
        AssistantService._select_agent_class_for_workflow.return_value = agent_class

        AssistantService.build_agent_for_workflow(
            user_input="hi", user=_user(), request_uuid="r", workflow_assistant=node, is_global=True
        )

    kwargs = agent_class.call_args.kwargs
    assert kwargs["assistant"].is_global is True
    assert kwargs["billing_assistant"] is saved
