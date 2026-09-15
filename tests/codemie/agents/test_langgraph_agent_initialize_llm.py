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

"""Tests for LangGraphAgent._initialize_llm() — router-abstraction migration.

These tests verify that _initialize_llm() delegates to create_router() and
router.build_chat_model() and forwards LLMParams correctly, without any
Switchyard-specific code inside the method.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_core.language_models import BaseChatModel

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.core.router import NULL_ROUTER
from codemie.core.router_chat_model import LLMParams


def _make_agent(
    llm_model: str = "gpt-4o",
    request_uuid: str = "req-123",
    temperature: float | None = 0.7,
    top_p: float | None = 0.9,
) -> LangGraphAgent:
    """Bypass LangGraphAgent.__init__ and set only the attributes _initialize_llm needs."""
    agent: LangGraphAgent = object.__new__(LangGraphAgent)
    agent.llm_model = llm_model
    agent.request_uuid = request_uuid
    agent.temperature = temperature
    agent.top_p = top_p
    return agent


class TestInitializeLLMUsesRouter:
    """_initialize_llm() must delegate to create_router() / router.build_chat_model()."""

    def test_create_router_called_with_model_name(self) -> None:
        """create_router is called with self.llm_model."""
        fake_llm = MagicMock(spec=BaseChatModel)
        mock_router = MagicMock()
        mock_router.build_chat_model.return_value = fake_llm

        agent = _make_agent(llm_model="gpt-4o")

        with patch("codemie.service.llm_service.router_factory.create_router", return_value=mock_router) as mock_create:
            agent._initialize_llm()

        mock_create.assert_called_once_with("gpt-4o")

    def test_build_chat_model_called_on_router(self) -> None:
        """router.build_chat_model is always called — never bypassed."""
        fake_llm = MagicMock(spec=BaseChatModel)
        mock_router = MagicMock()
        mock_router.build_chat_model.return_value = fake_llm

        agent = _make_agent(llm_model="gpt-4o", request_uuid="req-abc", temperature=0.5, top_p=0.8)

        with patch("codemie.service.llm_service.router_factory.create_router", return_value=mock_router):
            result = agent._initialize_llm()

        mock_router.build_chat_model.assert_called_once_with(
            model_name="gpt-4o",
            request_id="req-abc",
            llm_params=LLMParams(temperature=0.5, top_p=0.8),
        )
        assert result is fake_llm

    def test_llm_params_temperature_and_top_p_forwarded(self) -> None:
        """temperature and top_p from agent config reach LLMParams unchanged."""
        fake_llm = MagicMock(spec=BaseChatModel)
        mock_router = MagicMock()
        mock_router.build_chat_model.return_value = fake_llm

        agent = _make_agent(temperature=0.0, top_p=None)

        with patch("codemie.service.llm_service.router_factory.create_router", return_value=mock_router):
            agent._initialize_llm()

        (_, kwargs) = mock_router.build_chat_model.call_args
        assert kwargs["llm_params"].temperature == 0.0
        assert kwargs["llm_params"].top_p is None

    def test_null_router_build_chat_model_still_called(self) -> None:
        """Even when create_router returns NULL_ROUTER, build_chat_model is called on it."""
        fake_llm = MagicMock(spec=BaseChatModel)

        agent = _make_agent(llm_model="plain-model", request_uuid="req-xyz", temperature=None, top_p=None)

        # Patch Router.build_chat_model (inherited by NullRouter) so we don't need real credentials.
        with patch.object(NULL_ROUTER.__class__, "build_chat_model", return_value=fake_llm) as mock_bcm:
            with patch("codemie.service.llm_service.router_factory.create_router", return_value=NULL_ROUTER):
                result = agent._initialize_llm()

        mock_bcm.assert_called_once_with(
            model_name="plain-model",
            request_id="req-xyz",
            llm_params=LLMParams(temperature=None, top_p=None),
        )
        assert result is fake_llm

    def test_routing_log_only_fires_when_router_has_candidate_models(self) -> None:
        """The '[ROUTING] Per-call routing active' log must reflect real per-call routing —
        gated on candidate_models() being non-empty, not merely 'router is not NULL_ROUTER'.
        Both NULL_ROUTER (NullRouter) and the LiteLLM-router singleton (LiteLLMRouter) never
        decide synchronously and always report an empty candidate_models(), so logging
        "per-call routing active" for them would misrepresent what actually happens."""
        fake_llm = MagicMock(spec=BaseChatModel)

        passive_like_router = MagicMock()
        passive_like_router.name = "litellm_complexity"
        passive_like_router.candidate_models.return_value = ()
        passive_like_router.build_chat_model.return_value = fake_llm

        agent = _make_agent(llm_model="smart-router")

        with patch("codemie.agents.langgraph_agent.logger") as mock_logger:
            with patch("codemie.service.llm_service.router_factory.create_router", return_value=passive_like_router):
                agent._initialize_llm()

        mock_logger.info.assert_not_called()

    def test_routing_log_fires_when_router_has_candidate_models(self) -> None:
        """A router that actually offers candidate models (e.g. SwitchyardRouter) does log."""
        fake_llm = MagicMock(spec=BaseChatModel)

        active_router = MagicMock()
        active_router.name = "switchyard"
        active_router.candidate_models.return_value = ("claude-4-6-sonnet", "claude-4-5-haiku")
        active_router.build_chat_model.return_value = fake_llm

        agent = _make_agent(llm_model="claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal")

        with patch("codemie.agents.langgraph_agent.logger") as mock_logger:
            with patch("codemie.service.llm_service.router_factory.create_router", return_value=active_router):
                agent._initialize_llm()

        mock_logger.info.assert_called_once()
        assert "Per-call routing active" in mock_logger.info.call_args.args[0]

    def test_no_switchyard_specific_import_in_method(self) -> None:
        """_initialize_llm must NOT import from enterprise.switchyard.agent."""
        import ast
        import inspect
        import textwrap

        source = inspect.getsource(LangGraphAgent._initialize_llm)
        dedented = textwrap.dedent(source)
        tree = ast.parse(dedented)

        switchyard_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and isinstance(getattr(node, "module", None), str)
            and "switchyard.agent" in getattr(node, "module", "")
        ]
        assert not switchyard_imports, (
            "_initialize_llm still imports from enterprise.switchyard.agent; "
            "migrate to create_router()/router.build_chat_model() instead"
        )
