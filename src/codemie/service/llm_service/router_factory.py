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

"""``create_router()`` — the one public entry point for resolving a Router.

Lives at the service layer (not in ``core``) because it genuinely needs the service layer
and enterprise packages: catalog lookups via ``llm_service`` and construction of
``SwitchyardRouter``/``LiteLLMRouter`` instances. Per
``.ai-run/guides/architecture/layered-architecture.md``, ``core`` is the shared foundation
that services depend on — not the reverse — so this factory does not belong in ``core``.

``create_router()`` itself is a flat "try each resolver, first non-None wins" loop over
``_RESOLVERS`` rather than an if/elif chain: this isn't a plugin system (the set of routing
mechanisms is closed — each one is also a hand-maintained field on the LLMModel/LLMRouter
config schema, so a genuinely new mechanism always means a config-schema change too, not just
a Python-side registration), but keeping each mechanism's resolution as an independently
named, independently testable function — instead of nested branches sharing one function
body — means adding a mechanism is "write one more ``_resolve_*`` function with this same
``(model_name) -> Router | None`` shape and append it to the tuple," with zero changes to
``create_router()``'s own control flow or to any resolver already in the tuple.
"""

from __future__ import annotations

from collections.abc import Callable

from codemie.configs import logger
from codemie.core.router import NULL_ROUTER, Router


def _resolve_switchyard_router(model_name: str) -> Router | None:
    """Resolve *model_name* as a Switchyard alias — return None if it isn't one."""
    from codemie.service.llm_service.llm_service import llm_service

    if not llm_service.is_router_model(model_name):
        return None

    from codemie.enterprise.switchyard.engine import get_proxy_switchyard_router
    from codemie.enterprise.switchyard.router import SwitchyardRouter

    engine = get_proxy_switchyard_router(router_name=model_name)
    if engine is not None:
        return SwitchyardRouter(engine)
    logger.warning("[ROUTING] Switchyard model %r declared but engine absent; treating as NULL_ROUTER", model_name)
    return NULL_ROUTER


def _resolve_litellm_router(model_name: str) -> Router | None:
    """Resolve *model_name* as a declared LiteLLM auto-router — return None if its catalog
    entry doesn't say so. Constructs a fresh LiteLLMRouter every call (never cached/shared —
    see that class's own docstring): counterfactual_model is read from the same catalog
    lookup already needed to check is_declared_litellm_router(), and varies per alias, so a
    shared singleton would leak one alias's declared baseline onto every other."""
    from codemie.service.llm_service.llm_service import llm_service

    details = llm_service.get_model_details(model_name)
    if details is None or (details.base_name != model_name and details.deployment_name != model_name):
        return None
    if not details.is_declared_litellm_router():
        return None
    litellm_router_config = details.litellm_router
    assert litellm_router_config is not None  # guaranteed by is_declared_litellm_router()

    from codemie.enterprise.litellm.router import LiteLLMRouter

    return LiteLLMRouter(model_name, counterfactual_model=litellm_router_config.counterfactual_model)


# Tried in order; the first non-None result wins. See the module docstring for why this is a
# flat list of named resolvers rather than an if/elif chain.
_RESOLVERS: tuple[Callable[[str], Router | None], ...] = (
    _resolve_switchyard_router,
    _resolve_litellm_router,
)


def create_router(model_name: str) -> Router:
    """Every other piece of routing behavior — proxy dispatch, agent chat-model
    construction, classifier-usage tracking, billed-model resolution — is a method call on
    the instance this returns. Resolved fresh every call (cheap: catalog lookups only, no
    I/O) — never cached across calls, matching ProxySwitchyardRouter's own existing
    per-request-fresh construction."""
    for resolve in _RESOLVERS:
        router = resolve(model_name)
        if router is not None:
            return router
    return NULL_ROUTER
