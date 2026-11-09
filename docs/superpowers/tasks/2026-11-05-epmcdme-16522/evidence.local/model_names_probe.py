# ruff: noqa: T201
"""EPMCDME-16522: how the backend pricer resolves model names that coding agents report, against the LiteLLM catalog.

Run from the repo root: poetry run python <this file> [model ...]
"""

import sys
from unittest.mock import patch

import httpx

from codemie.configs import config
from codemie.configs.llm_config import ModelType
from codemie.enterprise.litellm.dependencies import _map_raw_models
from codemie.enterprise.litellm.models import get_model_type, map_litellm_to_llm_model
from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

DEFAULT = [
    # Claude Code
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-5",
    "us.anthropic.claude-sonnet-4-6",
    "claude-router-standard",
    "claude-router-premium",
    # Copilot / Codex / desktop
    "gpt-5.5-2026-04-24",
    "gpt-5.5",
    "gpt-5-codex",
    "mai-code-1.1-flash",
    "gpt-router-standard",
    # Gemini / open models
    "gemini-3.1-pro-preview",
    "qwen.qwen3-coder-480b-a35b-v1",
    "moonshotai.kimi-k2.5",
]
names = sys.argv[1:] or DEFAULT

resp = httpx.get(
    f"{config.LITE_LLM_URL.rstrip('/')}/model/info",
    headers={"Authorization": f"Bearer {config.LITE_LLM_MASTER_KEY}"},
    timeout=60,
)
resp.raise_for_status()
if "--raw" in names:  # every proxy entry, no dedup (NOT what the backend prices from)
    names.remove("--raw")
    catalog = [map_litellm_to_llm_model(m) for m in resp.json()["data"] if get_model_type(m) == ModelType.CHAT]
else:  # the backend's own build of llm_service._default_litellm_models: enabled only, deduped by base_name
    catalog = _map_raw_models(resp.json()["data"]).chat_models
print(f"catalog chat models: {len(catalog)}")
MODULE = "codemie.service.analytics.handlers.coding_agent_pricing"
counts = TokenCounts(input=1000, output=500, cache_read=40110, cache_creation=2000)
with (
    patch(f"{MODULE}.llm_service.get_all_llm_model_info", return_value=catalog),
    patch(f"{MODULE}.llm_service.catalog_source", return_value="litellm"),
):
    for name in names:
        pricer = CatalogPricer()
        cost = pricer.price(name, counts)
        cfg = pricer._cost(name)
        if cfg is None:
            verdict = "NOT_IN_CATALOG -> unpriced_models, cost_available=false"
        elif not cfg.input and not cfg.output:
            verdict = "ZERO_RATES -> silent $0 (ticket bug)"
        elif pricer.unpriced:
            verdict = "PARTLY_PRICED -> unpriced_models, cost_available=true, cost understated"
        else:
            verdict = "PRICED"
        rates = (
            None
            if cfg is None
            else (cfg.input, cfg.output, cfg.cache_read_input_token_cost, cfg.cache_creation_input_token_cost)
        )
        print(f"{name:<34} ${cost:<10.6f} rates(in,out,cr,cw)={rates}  {verdict}")
