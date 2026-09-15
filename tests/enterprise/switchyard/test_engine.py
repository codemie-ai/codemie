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

"""Live-catalog defense-in-depth check for router-on-router configurations — the request-time
counterpart to the static test_switchyard_capable_is_litellm_router_skipped /
test_switchyard_efficient_is_litellm_router_skipped tests in tests/codemie/configs/test_llm_config.py.
"""

from __future__ import annotations

from unittest.mock import patch

from codemie.configs.llm_config import (
    LiteLLMRouterConfig,
    LLMModel,
    LLMRouter,
    RoutingMode,
    SwitchyardConfig,
    SwitchyardTuning,
)
from codemie.enterprise.switchyard.engine import get_proxy_switchyard_router

_ROUTER_NAME = "cap-switchyard-eff-signal"


def _llm_router(capable_model: str = "cap", efficient_model: str = "eff") -> LLMRouter:
    return LLMRouter(
        base_name=_ROUTER_NAME,
        switchyard=SwitchyardConfig(
            capable_model=capable_model,
            efficient_model=efficient_model,
            mode=RoutingMode.SIGNAL,
            tuning=SwitchyardTuning(),
        ),
    )


def test_get_proxy_switchyard_router_rejects_capable_declared_as_litellm_router():
    with (
        patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", True),
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
    ):
        mock_service.get_llm_routers.return_value = [_llm_router()]
        mock_service.is_router_model.return_value = False
        mock_service.get_model_details.return_value = LLMModel(
            base_name="cap", deployment_name="cap", enabled=True, litellm_router=LiteLLMRouterConfig()
        )
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is None


def test_get_proxy_switchyard_router_rejects_efficient_that_is_a_switchyard_alias():
    with (
        patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", True),
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
    ):
        mock_service.get_llm_routers.return_value = [_llm_router()]
        # "cap" resolves cleanly; "eff" is itself a configured Switchyard router base_name.
        mock_service.is_router_model.side_effect = lambda name: name == "eff"
        mock_service.get_model_details.return_value = None
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is None


def test_get_proxy_switchyard_router_proceeds_when_neither_target_is_a_router():
    with (
        patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", True),
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
    ):
        mock_service.get_llm_routers.return_value = [_llm_router()]
        mock_service.is_router_model.return_value = False
        mock_service.get_model_details.return_value = LLMModel(base_name="cap", deployment_name="cap", enabled=True)
        mock_service.get_model_deployment_name.side_effect = lambda name: {"cap": "cap-dep", "eff": "eff-dep"}[name]
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is not None
    assert result.capable_model == "cap"
    assert result.efficient_model == "eff"
    assert mock_service.get_model_deployment_name.call_count == 2


def test_get_proxy_switchyard_router_rejects_target_not_in_catalog():
    """A capable/efficient model that no longer resolves in the live catalog (renamed, removed,
    or a typo) must reject the setup outright instead of silently falling back to the literal
    base_name as a deployment name."""
    with (
        patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", True),
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
    ):
        mock_service.get_llm_routers.return_value = [_llm_router()]
        mock_service.is_router_model.return_value = False
        mock_service.get_model_details.return_value = LLMModel(base_name="cap", deployment_name="cap", enabled=True)
        mock_service.get_model_deployment_name.return_value = None
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is None


def test_get_proxy_switchyard_router_returns_none_when_disabled():
    with patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", False):
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is None


def test_get_proxy_switchyard_router_returns_none_when_not_configured():
    with (
        patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", True),
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
    ):
        mock_service.get_llm_routers.return_value = []
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is None
