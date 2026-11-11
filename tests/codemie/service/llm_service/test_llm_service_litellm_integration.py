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

"""Tests for LLMService integration with LiteLLM enterprise layer.

These tests verify that LLMService correctly integrates with the LiteLLM
enterprise package for model management and gracefully falls back to YAML
configuration when LiteLLM is not available.
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.configs.llm_config import (
    LiteLLMModels,
    LiteLLMRouterConfig,
    LLMConfig,
    LLMModel,
    LLMProvider,
    ModelSwitchyard,
    RouterTier,
    RouterTiers,
    RoutingMode,
)
from codemie.service.llm_service.llm_service import LLMService


@pytest.fixture
def llm_service():
    """Create a basic LLMService instance for testing."""
    from codemie.configs.llm_config import llm_config

    return LLMService(llm_config)


@pytest.fixture
def mock_litellm_models():
    """Create mock LiteLLM models for testing."""
    chat_models = [
        LLMModel(
            base_name="litellm-gpt-4",
            deployment_name="litellm-gpt-4",
            enabled=True,
            label="LiteLLM GPT-4",
            provider=LLMProvider.AZURE_OPENAI,
            default=True,  # Mark as default for deployment name tests
        ),
        LLMModel(
            base_name="litellm-claude",
            deployment_name="litellm-claude",
            enabled=True,
            label="LiteLLM Claude",
            provider=LLMProvider.ANTHROPIC,
        ),
    ]
    embedding_models = [
        LLMModel(
            base_name="litellm-ada-002",
            deployment_name="litellm-ada-002",
            enabled=True,
            label="LiteLLM Ada",
            provider=LLMProvider.AZURE_OPENAI,
            default=True,  # Mark as default for deployment name tests
        )
    ]
    return LiteLLMModels(chat_models=chat_models, embedding_models=embedding_models)


class TestLiteLLMModelInitialization:
    """Test LiteLLM model initialization in LLMService."""

    def test_initialize_default_litellm_models(self, llm_service, mock_litellm_models):
        """Test initializing LiteLLM models in service."""
        # Initially empty
        assert llm_service.default_litellm_models == []
        assert llm_service.default_litellm_embeddings == []

        # Initialize with LiteLLM models
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        # Verify models are stored
        assert len(llm_service.default_litellm_models) == 2
        assert llm_service.default_litellm_models[0].base_name == "litellm-gpt-4"
        assert llm_service.default_litellm_models[1].base_name == "litellm-claude"

        # Verify embeddings are stored
        assert len(llm_service.default_litellm_embeddings) == 1
        assert llm_service.default_litellm_embeddings[0].base_name == "litellm-ada-002"

    def test_initialize_default_litellm_models_empty(self, llm_service):
        """Test initializing LiteLLM with empty model list."""
        empty_models = LiteLLMModels(chat_models=[], embedding_models=[])

        llm_service.initialize_default_litellm_models(empty_models)

        assert llm_service.default_litellm_models == []
        assert llm_service.default_litellm_embeddings == []


class TestLiteLLMModelFallback:
    """Test fallback behavior between LiteLLM and YAML config."""

    def test_get_all_llm_model_info_uses_litellm_when_enabled(self, llm_service, mock_litellm_models):
        """Test that get_all_llm_model_info returns LiteLLM models when initialized."""
        from codemie.configs.config import config

        # Initialize LiteLLM models
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", True):
            models = llm_service.get_all_llm_model_info()

            # Should return LiteLLM models
            assert len(models) == 2
            assert models[0].base_name == "litellm-gpt-4"
            assert models[1].base_name == "litellm-claude"

    def test_get_all_llm_model_info_uses_yaml_when_disabled(self, llm_service, mock_litellm_models):
        """Test that get_all_llm_model_info returns YAML models when LiteLLM disabled."""
        from codemie.configs.config import config

        # Initialize LiteLLM models (but proxy is disabled)
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", False):
            models = llm_service.get_all_llm_model_info()

            # Should return YAML config models (not LiteLLM)
            # We can't assert exact models since they come from actual config,
            # but we verify it's not the LiteLLM models
            assert all(model.base_name != "litellm-gpt-4" for model in models)

    def test_get_all_llm_model_info_uses_yaml_when_not_initialized(self, llm_service):
        """Test that get_all_llm_model_info returns YAML models when LiteLLM not initialized."""
        from codemie.configs.config import config

        # Don't initialize LiteLLM models
        with patch.object(config, "LLM_PROXY_ENABLED", True):
            models = llm_service.get_all_llm_model_info()

            # Should return YAML config models (LiteLLM not initialized)
            assert all(model.base_name != "litellm-gpt-4" for model in models)

    @pytest.mark.parametrize(
        ("proxy_enabled", "initialized", "expected"),
        [(True, True, "litellm"), (True, False, "yaml"), (False, True, "yaml")],
    )
    def test_catalog_source(self, llm_service, mock_litellm_models, proxy_enabled, initialized, expected):
        from codemie.configs.config import config

        if initialized:
            llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", proxy_enabled):
            assert llm_service.catalog_source() == expected

    def test_get_route_cost_reads_the_litellm_route_map_only(self, llm_service, mock_litellm_models):
        from codemie.configs.config import config
        from codemie.configs.llm_config import CostConfig

        route = "bedrock/eu.anthropic.claude-opus-4-8"
        cost = CostConfig(input=5.5e-06, output=2.75e-05)
        llm_service.initialize_default_litellm_models(
            mock_litellm_models.model_copy(update={"route_costs": {route: cost}})
        )

        with patch.object(config, "LLM_PROXY_ENABLED", True):
            assert llm_service.get_route_cost(route) == cost
            assert llm_service.get_route_cost("bedrock/global.anthropic.claude-opus-4-8") is None
        with patch.object(config, "LLM_PROXY_ENABLED", False):
            assert llm_service.get_route_cost(route) is None

    def test_a_reload_replaces_the_route_map(self, llm_service, mock_litellm_models):
        from codemie.configs.config import config
        from codemie.configs.llm_config import CostConfig

        removed, kept = "bedrock/eu.anthropic.claude-opus-4-8", "vertex_ai/claude-sonnet-4-6"
        first = {removed: CostConfig(input=5.5e-06, output=2.75e-05), kept: CostConfig(input=3e-06, output=1.5e-05)}
        llm_service.initialize_default_litellm_models(mock_litellm_models.model_copy(update={"route_costs": first}))
        second = {kept: CostConfig(input=3.3e-06, output=1.65e-05)}
        llm_service.initialize_default_litellm_models(mock_litellm_models.model_copy(update={"route_costs": second}))

        with patch.object(config, "LLM_PROXY_ENABLED", True):
            assert llm_service.get_route_cost(removed) is None
            assert llm_service.get_route_cost(kept).input == 3.3e-06

    def test_get_all_embedding_model_info_uses_litellm_when_enabled(self, llm_service, mock_litellm_models):
        """Test that get_all_embedding_model_info returns LiteLLM embeddings when initialized."""
        from codemie.configs.config import config

        # Initialize LiteLLM models
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", True):
            embeddings = llm_service.get_all_embedding_model_info()

            # Should return LiteLLM embeddings
            assert len(embeddings) == 1
            assert embeddings[0].base_name == "litellm-ada-002"

    def test_get_all_embedding_model_info_uses_yaml_when_disabled(self, llm_service, mock_litellm_models):
        """Test that get_all_embedding_model_info returns YAML embeddings when LiteLLM disabled."""
        from codemie.configs.config import config

        # Initialize LiteLLM models (but proxy is disabled)
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", False):
            embeddings = llm_service.get_all_embedding_model_info()

            # Should return YAML config embeddings (not LiteLLM)
            assert all(emb.base_name != "litellm-ada-002" for emb in embeddings)


class TestGetLlmRoutersSourceConsistency:
    """get_llm_routers must use the same either/or model source as get_all_llm_model_info —
    no merge between the static YAML and the live LiteLLM/DIAL catalog."""

    @staticmethod
    def _config(tmp_path, body: str) -> LLMConfig:
        yaml_file = tmp_path / "c.yaml"
        yaml_file.write_text(body)
        return LLMConfig(yaml_file=yaml_file)

    _YAML_WITH_SWITCHYARD = """
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    switchyard:
      - base_name: 'cap-switchyard-eff-signal'
        efficient: 'eff'
        mode: signal
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
"""

    def test_uses_yaml_switchyard_when_litellm_not_initialized(self, tmp_path):
        service = LLMService(self._config(tmp_path, self._YAML_WITH_SWITCHYARD))

        routers = service.get_llm_routers()

        assert {r.base_name for r in routers} == {"cap-switchyard-eff-signal"}

    def test_ignores_yaml_switchyard_when_litellm_enabled_with_no_live_declaration(self, tmp_path, monkeypatch):
        """When the proxy is enabled and initialized, routers come only from the live catalog —
        a switchyard block declared in the static YAML for the same base_name must not leak in."""
        from codemie.configs.config import config as app_config

        monkeypatch.setattr(app_config, "LLM_PROXY_ENABLED", True)
        service = LLMService(self._config(tmp_path, self._YAML_WITH_SWITCHYARD))
        service.initialize_default_litellm_models(
            LiteLLMModels(
                chat_models=[
                    LLMModel(base_name="cap", deployment_name="cap", enabled=True),
                    LLMModel(base_name="eff", deployment_name="eff", enabled=True),
                ]
            )
        )

        routers = service.get_llm_routers()

        assert routers == []

    def test_uses_live_catalog_switchyard_when_litellm_enabled(self, tmp_path, monkeypatch):
        from codemie.configs.config import config as app_config

        monkeypatch.setattr(app_config, "LLM_PROXY_ENABLED", True)
        yaml_no_switchyard = """
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
embeddings_models: []
"""
        service = LLMService(self._config(tmp_path, yaml_no_switchyard))
        service.initialize_default_litellm_models(
            LiteLLMModels(
                chat_models=[
                    LLMModel(
                        base_name="cap",
                        deployment_name="cap",
                        enabled=True,
                        switchyard=[
                            ModelSwitchyard(
                                base_name="cap-switchyard-eff-signal", efficient="eff", mode=RoutingMode.SIGNAL
                            )
                        ],
                    ),
                    LLMModel(base_name="eff", deployment_name="eff", enabled=True),
                ]
            )
        )

        routers = service.get_llm_routers()

        assert {r.base_name for r in routers} == {"cap-switchyard-eff-signal"}

    def test_router_option_carries_switchyard_type_strategy_and_tiers(self, tmp_path):
        """A switchyard router's REST projection must carry router_type='switchyard', the
        mode as strategy, and the fixed 4-tier map — efficient serving simple/medium, capable
        serving complex/reasoning (the platform's chosen mapping for a 2-model router)."""
        yaml_body = """
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    label: 'Capable'
    enabled: true
    switchyard:
      - base_name: 'cap-switchyard-eff-classifier'
        efficient: 'eff'
        mode: classifier
        classifier_model: 'eff'
  - base_name: 'eff'
    deployment_name: 'eff'
    label: 'Efficient'
    enabled: true
embeddings_models: []
"""
        service = LLMService(self._config(tmp_path, yaml_body))

        options = service.get_allowed_router_options()

        option = next(o for o in options if o.base_name == "cap-switchyard-eff-classifier")
        assert option.router_type == "switchyard"
        assert option.strategy == RoutingMode.CLASSIFIER
        assert option.classifier_model == "eff"
        assert option.tiers is not None
        assert option.tiers.simple.model == "eff"
        assert option.tiers.simple.label == "Efficient"
        assert option.tiers.medium.model == "eff"
        assert option.tiers.complex.model == "cap"
        assert option.tiers.complex.label == "Capable"
        assert option.tiers.reasoning.model == "cap"

    def test_router_option_is_premium_checks_own_base_name_not_capable_efficient(self, tmp_path):
        """A switchyard router's is_premium uses the exact same check as any regular model —
        is_premium_model(base_name) — applied to the ROUTER's own base_name, not an OR across
        its capable/efficient pair. A router named without a premium alias must report False
        even when its capable model is a premium-alias-matching deployment."""
        from codemie.configs.config import config as app_config
        from codemie.configs.budget_config import budget_config
        from codemie.configs.config import PredefinedBudgetConfig
        from codemie.enterprise.litellm.dependencies import is_premium_model, is_premium_models_enabled

        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()
        yaml_body = """
llm_models:
  - base_name: 'claude-opus-5'
    deployment_name: 'claude-opus-5'
    enabled: true
    switchyard:
      - base_name: 'sy-signal-claude'
        efficient: 'eff'
        mode: signal
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
"""
        current = [b for b in budget_config.predefined_budgets if b.budget_category != "premium_models"]
        current.append(
            PredefinedBudgetConfig(
                budget_id="premium_models",
                name="Premium",
                description=None,
                soft_budget=0.0,
                max_budget=0.0,
                budget_duration="30d",
                budget_category="premium_models",
            )
        )
        try:
            with (
                patch.object(budget_config, "predefined_budgets", current),
                patch.object(app_config, "LITELLM_PREMIUM_MODELS_ALIASES", ["opus"]),
            ):
                is_premium_models_enabled.cache_clear()
                is_premium_model.cache_clear()
                service = LLMService(self._config(tmp_path, yaml_body))
                options = service.get_allowed_router_options()
        finally:
            is_premium_models_enabled.cache_clear()
            is_premium_model.cache_clear()

        option = next(o for o in options if o.base_name == "sy-signal-claude")
        assert option.is_premium is False

    def test_router_option_classifier_model_none_for_signal_strategy(self, tmp_path):
        service = LLMService(self._config(tmp_path, self._YAML_WITH_SWITCHYARD))

        options = service.get_allowed_router_options()

        option = next(o for o in options if o.base_name == "cap-switchyard-eff-signal")
        assert option.router_type == "switchyard"
        assert option.strategy == RoutingMode.SIGNAL
        assert option.classifier_model is None

    def test_router_option_inherits_provider_from_capable_model(self, tmp_path):
        """get_allowed_router_options projects the router's inherited provider through to the
        REST-facing LlmRouterOption, same as it already does for multimodal/supports_tools."""
        yaml_with_provider = """
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    provider: 'aws_bedrock'
    switchyard:
      - base_name: 'cap-switchyard-eff-signal'
        efficient: 'eff'
        mode: signal
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
"""
        service = LLMService(self._config(tmp_path, yaml_with_provider))

        options = service.get_allowed_router_options()

        option = next(o for o in options if o.base_name == "cap-switchyard-eff-signal")
        assert option.provider == LLMProvider.AWS_BEDROCK


class TestLiteLLMDeploymentNames:
    """Test deployment name resolution with LiteLLM models."""

    def test_get_llm_deployment_name_from_litellm(self, llm_service, mock_litellm_models):
        """Test getting deployment name from LiteLLM models."""
        from codemie.configs.config import config

        # Initialize LiteLLM models
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", True):
            deployment_name = llm_service.get_llm_deployment_name("litellm-gpt-4")

            assert deployment_name == "litellm-gpt-4"

    def test_get_embedding_deployment_name_from_litellm(self, llm_service, mock_litellm_models):
        """Test getting embedding deployment name from LiteLLM models."""
        from codemie.configs.config import config

        # Initialize LiteLLM models
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", True):
            deployment_name = llm_service.get_embedding_deployment_name("litellm-ada-002")

            assert deployment_name == "litellm-ada-002"


def _tiers(simple: str, medium: str, complex_: str, reasoning: str) -> RouterTiers:
    return RouterTiers(
        simple=RouterTier(model=simple),
        medium=RouterTier(model=medium),
        complex=RouterTier(model=complex_),
        reasoning=RouterTier(model=reasoning),
    )


def _chat_model(base_name: str, max_output_tokens=None, **overrides) -> LLMModel:
    return LLMModel(
        base_name=base_name,
        deployment_name=base_name,
        label=base_name,
        enabled=True,
        provider=LLMProvider.AWS_BEDROCK,
        max_output_tokens=max_output_tokens,
        **overrides,
    )


class TestRouterMaxOutputTokens:
    """A router's output limit is the smallest positive max_output_tokens across its distinct tier models;
    any unknown tier leaves it unset (EPMCDME-16835)."""

    def test_returns_min_across_distinct_tiers(self):
        by_name = {"a": _chat_model("a", 128000), "b": _chat_model("b", 64000), "c": _chat_model("c", 128000)}

        assert LLMService._router_max_output_tokens(_tiers("a", "a", "b", "c"), by_name) == 64000

    def test_shared_tier_model_resolves_once(self):
        by_name = {"a": _chat_model("a", 128000), "b": _chat_model("b", 128000)}

        assert LLMService._router_max_output_tokens(_tiers("a", "a", "b", "b"), by_name) == 128000

    def test_none_when_tiers_absent(self):
        assert LLMService._router_max_output_tokens(None, {"a": _chat_model("a", 128000)}) is None

    def test_none_when_any_tier_missing_from_catalog(self):
        by_name = {"a": _chat_model("a", 128000), "b": _chat_model("b", 64000)}

        assert LLMService._router_max_output_tokens(_tiers("a", "a", "b", "missing"), by_name) is None

    def test_none_when_any_tier_has_no_limit(self):
        by_name = {"a": _chat_model("a", 128000), "b": _chat_model("b", None)}

        assert LLMService._router_max_output_tokens(_tiers("a", "a", "b", "b"), by_name) is None

    @pytest.mark.parametrize("value", [0, -1, True])
    def test_tier_value_must_be_positive_int(self, value):
        # model_copy skips validation, so True stays a bool instead of being coerced to 1
        by_name = {"a": _chat_model("a", 128000), "b": _chat_model("b").model_copy(update={"max_output_tokens": value})}

        assert LLMService._router_max_output_tokens(_tiers("a", "a", "b", "b"), by_name) is None

    def test_tier_string_value_is_unknown(self):
        tier_b = MagicMock()
        tier_b.max_output_tokens = "128000"
        by_name = {"a": _chat_model("a", 128000), "b": tier_b}

        assert LLMService._router_max_output_tokens(_tiers("a", "a", "b", "b"), by_name) is None

    def test_tier_naming_the_router_itself_is_unknown(self):
        router = _chat_model(
            "router", None, litellm_router=LiteLLMRouterConfig(tiers=_tiers("a", "a", "router", "router"))
        )
        by_name = {"a": _chat_model("a", 128000), "router": router}

        assert LLMService._router_max_output_tokens(router.litellm_router.tiers, by_name) is None


class TestRouterOutputLimitOnCatalogLoad:
    """Storing the LiteLLM catalog fills in a router's missing output limit from its tiers (EPMCDME-16835)."""

    ROUTER = "claude-router-standard"

    @classmethod
    def _router(cls, max_output_tokens=None) -> LLMModel:
        return _chat_model(
            cls.ROUTER,
            max_output_tokens,
            litellm_router=LiteLLMRouterConfig(tiers=_tiers("a", "a", "b", "b")),
        )

    @classmethod
    def _stored_router(cls, service: LLMService) -> LLMModel:
        return next(m for m in service.default_litellm_models if m.base_name == cls.ROUTER)

    def test_router_without_limit_gets_min_of_tiers(self, llm_service):
        models = [self._router(), _chat_model("a", 128000), _chat_model("b", 64000)]

        llm_service.initialize_default_litellm_models(LiteLLMModels(chat_models=models))

        assert self._stored_router(llm_service).max_output_tokens == 64000

    def test_router_own_positive_limit_wins(self, llm_service):
        models = [self._router(32000), _chat_model("a", 128000), _chat_model("b", 64000)]

        llm_service.initialize_default_litellm_models(LiteLLMModels(chat_models=models))

        assert self._stored_router(llm_service).max_output_tokens == 32000

    def test_router_invalid_own_limit_is_derived(self, llm_service):
        models = [self._router(0), _chat_model("a", 128000), _chat_model("b", 64000)]

        llm_service.initialize_default_litellm_models(LiteLLMModels(chat_models=models))

        assert self._stored_router(llm_service).max_output_tokens == 64000

    def test_router_with_unknown_tier_stays_unset(self, llm_service):
        models = [self._router(), _chat_model("a", 128000)]

        llm_service.initialize_default_litellm_models(LiteLLMModels(chat_models=models))

        assert self._stored_router(llm_service).max_output_tokens is None

    def test_non_router_models_untouched(self, llm_service):
        models = [self._router(), _chat_model("a", 128000), _chat_model("b", 64000), _chat_model("plain", None)]

        llm_service.initialize_default_litellm_models(LiteLLMModels(chat_models=models))

        limits = {m.base_name: m.max_output_tokens for m in llm_service.default_litellm_models}
        assert limits == {self.ROUTER: 64000, "a": 128000, "b": 64000, "plain": None}

    def test_logs_derived_limit(self, llm_service):
        models = [self._router(), _chat_model("a", 128000), _chat_model("b", 64000)]

        with patch("codemie.service.llm_service.llm_service.logger") as mock_logger:
            llm_service.initialize_default_litellm_models(LiteLLMModels(chat_models=models))

        lines = [call.args[0] for call in mock_logger.info.call_args_list]
        assert (
            f"router_output_limit model={self.ROUTER} max_output_tokens=64000 source=router_tiers forwarded=true"
            in lines
        )

    def test_router_alias_without_claude_over_claude_tiers_is_marked_claude_family(self, llm_service):
        tiers = _tiers("claude-haiku", "claude-haiku", "opus-latest", "opus-latest")
        router = _chat_model("router-standard", None, litellm_router=LiteLLMRouterConfig(tiers=tiers))
        tier_a, tier_b = _chat_model("claude-haiku", 128000), _chat_model("opus-latest", 64000, claude_family=True)

        with patch("codemie.service.llm_service.llm_service.logger") as mock_logger:
            llm_service.initialize_default_litellm_models(LiteLLMModels(chat_models=[router, tier_a, tier_b]))

        stored = next(m for m in llm_service.default_litellm_models if m.base_name == "router-standard")
        assert stored.claude_family is True
        assert stored.max_output_tokens == 64000
        lines = [call.args[0] for call in mock_logger.info.call_args_list]
        assert any(line.endswith("forwarded=true") for line in lines if line.startswith("router_output_limit"))

    def test_router_over_non_claude_tiers_logs_that_nothing_is_forwarded(self, llm_service):
        router = _chat_model("router-gpt", None, litellm_router=LiteLLMRouterConfig(tiers=_tiers("a", "a", "b", "b")))

        with patch("codemie.service.llm_service.llm_service.logger") as mock_logger:
            llm_service.initialize_default_litellm_models(
                LiteLLMModels(chat_models=[router, _chat_model("a", 128000), _chat_model("b", 64000)])
            )

        stored = next(m for m in llm_service.default_litellm_models if m.base_name == "router-gpt")
        assert stored.claude_family is False
        lines = [call.args[0] for call in mock_logger.info.call_args_list]
        assert (
            "router_output_limit model=router-gpt max_output_tokens=64000 source=router_tiers forwarded=false" in lines
        )

    def test_reload_recomputes_the_limit_from_the_new_tiers(self, llm_service):
        llm_service.initialize_default_litellm_models(
            LiteLLMModels(chat_models=[self._router(), _chat_model("a", 128000), _chat_model("b", 64000)])
        )

        llm_service.initialize_default_litellm_models(
            LiteLLMModels(chat_models=[self._router(), _chat_model("a", 128000), _chat_model("b", 32000)])
        )

        assert self._stored_router(llm_service).max_output_tokens == 32000

    def test_warns_when_router_limit_cannot_be_derived(self, llm_service):
        models = [self._router(), _chat_model("a", 128000)]

        with patch("codemie.service.llm_service.llm_service.logger") as mock_logger:
            llm_service.initialize_default_litellm_models(LiteLLMModels(chat_models=models))

        mock_logger.warning.assert_called_once_with(
            f"router_output_limit model={self.ROUTER} max_output_tokens=unset source=unresolved_tiers"
        )

    def test_does_not_warn_when_router_limit_is_derived(self, llm_service):
        models = [self._router(), _chat_model("a", 128000), _chat_model("b", 64000)]

        with patch("codemie.service.llm_service.llm_service.logger") as mock_logger:
            llm_service.initialize_default_litellm_models(LiteLLMModels(chat_models=models))

        mock_logger.warning.assert_not_called()

    def test_does_not_log_when_router_limit_is_its_own(self, llm_service):
        models = [self._router(32000), _chat_model("a", 128000), _chat_model("b", 64000)]

        with patch("codemie.service.llm_service.llm_service.logger") as mock_logger:
            llm_service.initialize_default_litellm_models(LiteLLMModels(chat_models=models))

        lines = [call.args[0] for call in mock_logger.info.call_args_list]
        assert not any(line.startswith("router_output_limit") for line in lines)
