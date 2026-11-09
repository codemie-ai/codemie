# ruff: noqa: T201
"""EPMCDME-16522 reproduction: zero-rate models are not reported as unpriced.

Standalone, no DB. Run from the repo root:
    poetry run python docs/superpowers/tasks/2026-11-05-epmcdme-16522/evidence.local/repro_unpriced.py
Patches the catalog the same way tests/.../test_coding_agent_pricing.py does.
"""

from unittest.mock import MagicMock, patch

from codemie.configs.llm_config import CostConfig
from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts, lookup_cost_config
from codemie.service.analytics.handlers.session_money import has_priced_model, price_session_models

MODULE = "codemie.service.analytics.handlers.coding_agent_pricing"
MODEL = "gpt-5.5-2026-04-24"
CACHE_READ_TOKENS = 40110


def entry(cost: CostConfig | None) -> MagicMock:
    m = MagicMock()
    m.base_name = MODEL
    m.deployment_name = None
    m.cost = cost
    return m


CASES = {
    "A. no catalog entry at all (control: mai-code-1.1-flash style)": [],
    "B. entry, cost=None": [entry(None)],
    "C. entry, in=0 out=0 cache_read=None (ticket's claimed shape)": [
        entry(CostConfig(input=0.0, output=0.0, cache_read_input_token_cost=None))
    ],
    "D. entry, in=0 out=0 cache_read=0.0": [entry(CostConfig(input=0.0, output=0.0, cache_read_input_token_cost=0.0))],
    "E. real rates (control: gpt-6-luna style)": [
        entry(CostConfig(input=3e-6, output=1.5e-5, cache_read_input_token_cost=1e-8))
    ],
}

with patch(f"{MODULE}.llm_service.catalog_source", return_value="litellm"):
    for name, catalog in CASES.items():
        with patch(f"{MODULE}.llm_service.get_all_llm_model_info", return_value=catalog):
            pricer = CatalogPricer()
            cfg = lookup_cost_config(MODEL)
            cost = pricer.price(MODEL, TokenCounts(cache_read=CACHE_READ_TOKENS))
            money = price_session_models(
                [{"session_id": "s", "model_name": MODEL, "cache_read_tokens": CACHE_READ_TOKENS}]
            )
            print(f"--- {name}")
            print(f"  lookup_cost_config      -> {cfg}")
            print(f"  price(cache_read=40110) -> {cost!r}   pricer.unpriced={pricer.unpriced}")
            print(
                f"  price_session_models    -> total={money.total!r} cache_read={money.cache_read!r} "
                f"unpriced={money.pricer.unpriced}"
            )
            print(f"  cost_available (has_priced_model) -> {has_priced_model([MODEL], CatalogPricer())}")
