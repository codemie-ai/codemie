# ruff: noqa: T201
"""EPMCDME-16522 blast radius: the LiteLLM proxy catalog as the backend parses it.

Fetches GET {LITE_LLM_URL}/model/info with the .env master key (never printed), maps each entry through the
backend's own map_litellm_to_llm_model / get_model_type, and classifies the resulting CostConfig.
Run from the repo root: poetry run python <this file> > litellm_catalog_audit.json
"""

import json
import sys

import httpx

from codemie.configs import config
from codemie.configs.llm_config import ModelType
from codemie.enterprise.litellm.models import get_model_type, map_litellm_to_llm_model


def classify(cost) -> str:
    if cost is None:
        return "NO_COST"  # lookup None -> flagged unpriced today
    zero_io = not cost.input and not cost.output
    if zero_io and cost.cache_read_input_token_cost is None:
        return "ZERO_IO_CACHE_NONE"  # cost_available=true; in/out silently $0; cache-read tokens flagged
    if zero_io:
        return "ZERO_IO_CACHE_ZERO"  # silent $0 for everything, never flagged
    if cost.cache_read_input_token_cost is None:
        return "PRICED_NO_CACHE_READ"  # cache-read tokens flagged unpriced
    if cost.cache_read_input_token_cost == 0:
        return "PRICED_CACHE_READ_ZERO"  # cache-read silently $0
    return "PRICED"


resp = httpx.get(
    f"{config.LITE_LLM_URL.rstrip('/')}/model/info",
    headers={"Authorization": f"Bearer {config.LITE_LLM_MASTER_KEY}"},
    timeout=60,
)
resp.raise_for_status()
raw = resp.json().get("data", [])
rows = []
for item in raw:
    mtype = get_model_type(item)
    info = item.get("model_info", {}) or {}
    try:
        model = map_litellm_to_llm_model(item)
        cost = model.cost
        base, dep = model.base_name, model.deployment_name
    except Exception as exc:  # report, never hide
        cost, base, dep = None, item.get("model_name"), f"MAP_ERROR {exc}"
    rows.append(
        {
            "model_name": item.get("model_name"),
            "base_name": base,
            "deployment_name": dep,
            "mode": mtype.value if mtype else None,
            "is_chat": mtype == ModelType.CHAT,
            "class": classify(cost),
            "raw_input": info.get("input_cost_per_token"),
            "raw_output": info.get("output_cost_per_token"),
            "raw_cache_read": info.get("cache_read_input_token_cost"),
            "raw_cache_creation": info.get("cache_creation_input_token_cost"),
            "raw_cache_creation_1h": info.get("cache_creation_input_token_cost_above_1hr"),
            "raw_per_second": info.get("input_cost_per_second"),
            "litellm_model": (item.get("litellm_params") or {}).get("model"),
        }
    )
print(f"entries={len(rows)}", file=sys.stderr)
json.dump(rows, sys.stdout, indent=1, default=str)
