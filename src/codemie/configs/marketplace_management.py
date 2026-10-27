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

"""Pure-Python config for marketplace management verification.

All defaults live here in code — no YAML file is read at startup.
Admin overrides mutate MARKETPLACE_MANAGEMENT_DEFAULTS in-process via
get_marketplace_management_config(); no file I/O is needed.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from codemie.configs.config import config
from codemie.service.customer_config_declarations import build_key
from codemie.service.dynamic_config_service import DynamicConfigService

_logger = logging.getLogger(__name__)


class VerificationCoreGates(BaseModel):
    description_min: int = 100
    system_prompt_min: int = 200
    # The specified value (EPMCDME-14231), restored in EXECUTION-PLAN 9.7. It
    # shipped at 800 with no record of why. Measured against the live corpus
    # before moving it: of 219 audited assistants 58 configure no tool / MCP /
    # skill / datasource, and 56 of those clear both 800 and 500 -- zero rows
    # change verdict. With no measurable cost, the config moves to the spec
    # rather than the spec moving to the config (CHANGE-BRAINSTORM D3b-gates).
    substance_prompt_alt_min: int = 500


class VerificationPolishGates(BaseModel):
    description_min: int = 250
    categories_min: int = 2
    require_icon: bool = True
    conversation_starters_min: int = 2


class VerificationSimilarity(BaseModel):
    # INERT in v1. No dense-vector text backend is wired: the field is retained
    # only because it is part of the `GET/PUT /entities/{kind}/config` contract
    # and because it names the seam a funded vector-rehabilitation follow-up
    # would plug into. Nothing reads it to choose behaviour any more -- the
    # backend factory it used to select was deleted with the rest of the
    # unreachable vector path (F-71). See CHANGE-BRAINSTORM D3b.
    text_backend: str = "elasticsearch"
    critical_threshold: float = 0.70
    optional_threshold: float = 0.55
    report_threshold: float = 0.40
    shortlist_size: int = 10


class MarketplaceManagementConfig(BaseModel):
    enabled: bool = True
    hot_min_users: int = 50
    warm_min_users: int = 7
    cold_min_users: int = 3
    grace_days: int = 60
    archive_dead_after_days: int = 30
    core_gates: VerificationCoreGates = Field(default_factory=VerificationCoreGates)
    polish_gates: VerificationPolishGates = Field(default_factory=VerificationPolishGates)
    similarity: VerificationSimilarity = Field(default_factory=VerificationSimilarity)
    # Similarity weights. MUST sum to 1.0 -- SimilarityConfig.validate()
    # rejects anything else, and it now runs on the live path
    # (Config.from_marketplace_management), not only on the legacy YAML one.
    #
    # v1 similarity has three mechanisms and only the first of them is
    # weighted (decision D3b, recorded in CHANGE-BRAINSTORM):
    #
    #   1. this weighted sum over STRUCTURAL signals;
    #   2. an exact normalized-name override;
    #   3. a recorded clone-edge override, read from `assistant_clone_event`.
    #
    # The three text signals -- `description`, `system_prompt` and `purpose` --
    # were scored by a dense-vector text backend, and the only wired backend
    # returned None from fit_transform(), so they collapsed to 0.0 for every
    # pair. They previously carried 0.45 of the weight budget: dead weight that
    # both broke the sum-to-1.0 invariant (the dict shipped summing to 1.10)
    # and capped every combined score at 0.65 -- below the 0.70 OPTIONAL
    # threshold, so no pair could ever be flagged on score alone. They are
    # dropped, and `SimilarityConfig.validate()` now REJECTS them rather than
    # accepting a budget nothing can spend.
    #
    # The surviving structural weights keep their previous ratios, renormalised
    # over the 0.65 that was live (e.g. skills 0.17/0.65 -> 0.26), so relative
    # signal importance is unchanged.
    #
    # `clone` is deliberately NOT a weight. A recorded clone edge is provenance
    # -- someone literally created one of these assistants from the other --
    # not an approximation to be blended with Jaccard overlaps. It is an
    # override, exactly like the exact-name rule beside it: it pins the pair to
    # 1.0/CRITICAL, but only while the structural score above is itself still
    # at or above the critical threshold, so a clone the owner has since
    # reworked is judged on its current content rather than marked a duplicate
    # forever (MR !4070 review comment #23). See `similarity.score_pair`.
    #
    # Deferred (vector rehabilitation): reinstating meaningful text similarity is a
    # funded follow-up, not a config tweak -- it needs a real dense-vector
    # backend, an index backfill, batched msearch, a cosine rescale and a
    # threshold recalibration against measurement. When that lands, reintroduce
    # `description`, `system_prompt` and `purpose` here, lift the rejection in
    # `SimilarityConfig.validate()`, and renormalise the whole dict together.
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "name": 0.08,
            "tools": 0.23,
            "mcp_servers": 0.20,
            "skills": 0.26,
            "datasources": 0.15,
            "categories": 0.08,
        }
    )


# -------- Per-kind registry ------------------------------------------------
#
# Each marketplace entity type (assistants today, workflows next) gets its
# own config instance so assessment rules can diverge — e.g. workflows may
# demand different substance thresholds, weight components, or gate rules
# that don't apply to assistants at all. The registry is keyed on the
# lowercase kind name (the same string used in API paths:
# `/entities/assistants/config`, `/entities/workflows/config`).
#
# Adding a new kind: append it to KIND_DEFAULTS below with whichever
# tweaked defaults suit that entity type. No other file needs to change.

_KIND_DEFAULTS: dict[str, MarketplaceManagementConfig] = {
    "assistants": MarketplaceManagementConfig(),
    # Workflows start with the same defaults but are a separate instance —
    # once workflow-specific gate rules are known they can be overridden
    # here (or edited at runtime via PUT /entities/workflows/config).
    "workflows": MarketplaceManagementConfig(),
}

# Back-compat alias: some legacy tests still import
# MARKETPLACE_MANAGEMENT_DEFAULTS. Points at the assistants config so
# existing behaviour is unchanged.
MARKETPLACE_MANAGEMENT_DEFAULTS: MarketplaceManagementConfig = _KIND_DEFAULTS["assistants"]


_THRESHOLD_DEFAULTS: dict[str, tuple[str, int]] = {
    "hotMinUsers": ("hot_min_users", 50),
    "warmMinUsers": ("warm_min_users", 7),
    "coldMinUsers": ("cold_min_users", 3),
    "graceDays": ("grace_days", 60),
    "archiveDeadAfterDays": ("archive_dead_after_days", 30),
}


def _read_threshold_overrides() -> dict:
    """Read marketplaceThresholds from customer_config storage.

    Returns a dict of model field overrides (e.g. {"hot_min_users": 100})
    or an empty dict if no override is stored or an error occurs.
    """
    try:
        row = DynamicConfigService.get_by_key(build_key("marketplaceThresholds"))
    except Exception:
        return {}

    if row is None:
        return {}

    try:
        data = json.loads(row.value)
    except (ValueError, TypeError):
        return {}

    result = {}
    for json_key, (model_key, default) in _THRESHOLD_DEFAULTS.items():
        raw = data.get(json_key)
        try:
            result[model_key] = int(raw) if raw is not None else default
        except (ValueError, TypeError):
            result[model_key] = default

    return result


def _read_core_gates_overrides() -> dict:
    """Read marketplaceCoreGates overrides from customer_config storage."""
    try:
        row = DynamicConfigService.get_by_key(build_key("marketplaceCoreGates"))
    except Exception:
        return {}
    if row is None:
        return {}
    try:
        data = json.loads(row.value)
        result = {}
        if "descriptionMin" in data:
            result["description_min"] = int(data["descriptionMin"])
        if "systemPromptMin" in data:
            result["system_prompt_min"] = int(data["systemPromptMin"])
        if "substancePromptAltMin" in data:
            result["substance_prompt_alt_min"] = int(data["substancePromptAltMin"])
        return result
    except (KeyError, ValueError, TypeError):
        return {}


def _read_polish_gates_overrides() -> dict:
    """Read marketplacePolishGates overrides from customer_config storage."""
    try:
        row = DynamicConfigService.get_by_key(build_key("marketplacePolishGates"))
    except Exception:
        return {}
    if row is None:
        return {}
    try:
        data = json.loads(row.value)
        result = {}
        if "descriptionMin" in data:
            result["description_min"] = int(data["descriptionMin"])
        if "categoriesMin" in data:
            result["categories_min"] = int(data["categoriesMin"])
        if "requireIcon" in data:
            result["require_icon"] = bool(data.get("requireIcon", False))
        if "conversationStartersMin" in data:
            result["conversation_starters_min"] = int(data["conversationStartersMin"])
        return result
    except (KeyError, ValueError, TypeError):
        return {}


def _read_similarity_overrides() -> dict:
    """Read marketplaceSimilarity overrides from customer_config storage."""
    try:
        row = DynamicConfigService.get_by_key(build_key("marketplaceSimilarity"))
    except Exception:
        return {}
    if row is None:
        return {}
    try:
        data = json.loads(row.value)
        result = {}
        if "criticalThreshold" in data:
            result["critical_threshold"] = float(data["criticalThreshold"])
        if "optionalThreshold" in data:
            result["optional_threshold"] = float(data["optionalThreshold"])
        if "reportThreshold" in data:
            result["report_threshold"] = float(data["reportThreshold"])
        if "shortlistSize" in data:
            result["shortlist_size"] = int(data["shortlistSize"])
        return result
    except (KeyError, ValueError, TypeError):
        return {}


def get_marketplace_management_config(
    kind: str = "assistants",
) -> MarketplaceManagementConfig:
    """Return the config for a specific entity kind with DB threshold overrides.

    `kind` is the lowercase plural form used in API paths — currently
    "assistants" or "workflows". The five threshold fields (hot_min_users,
    warm_min_users, cold_min_users, grace_days, archive_dead_after_days) are read from the
    customer_config DB on every call so that admin overrides stored via
    PUT /v1/config/declarations/marketplaceThresholds are reflected immediately.
    All other fields (core_gates, polish_gates, etc.) come from _KIND_DEFAULTS.
    Raises KeyError for unknown kinds.
    """
    key = kind.lower()
    if key not in _KIND_DEFAULTS:
        raise KeyError(f"Unknown marketplace management kind: {kind!r}")

    base = _KIND_DEFAULTS[key]

    thresholds = _read_threshold_overrides()
    if thresholds:
        base = base.model_copy(update=thresholds)

    sub_updates: dict = {}
    if core := _read_core_gates_overrides():
        sub_updates["core_gates"] = base.core_gates.model_copy(update=core)
    if polish := _read_polish_gates_overrides():
        sub_updates["polish_gates"] = base.polish_gates.model_copy(update=polish)
    if sim := _read_similarity_overrides():
        sub_updates["similarity"] = base.similarity.model_copy(update=sim)
    return base.model_copy(update=sub_updates) if sub_updates else base


def is_marketplace_management_enabled(kind: str = "assistants") -> bool:
    """Return True when the marketplace management feature is switched on.

    Two switches, both of which must be on:
    * ``config.MARKETPLACE_MANAGEMENT_ENABLED`` — deployment-level kill switch
      (env var), read at request/registration time so it can be flipped per
      environment without a code change.
    * the per-kind ``MarketplaceManagementConfig.enabled`` field, which admins
      may mutate in-process via ``get_marketplace_management_config()``.

    Unknown kinds are treated as disabled rather than raising, so a caller
    asking about a kind that is not registered never breaks app startup.
    """
    if not config.MARKETPLACE_MANAGEMENT_ENABLED:
        return False
    try:
        return get_marketplace_management_config(kind).enabled
    except KeyError:
        return False


def list_supported_kinds() -> list[str]:
    """Return the ordered list of kinds the registry knows about."""
    return list(_KIND_DEFAULTS.keys())


def is_approval_gate_armed(kind: str = "assistants") -> bool:
    """Return True when publishing queues for review and edits trigger re-review.

    The gate needs somewhere to be decided: the approve, reject and force-publish
    routes are only mounted when marketplace management is on. With the gate flag
    on and management off every publish would land in PENDING_REVIEW with nobody
    able to approve it, so the gate is armed only when both are on. This is the
    single source for publish, edit re-review, rollback, the preflight check, and
    the `features:marketplaceApprovalGate` flag the UI reads.

    The gate flag is tested first so a deployment with it off never pays for the
    management lookup.
    """
    return bool(config.MARKETPLACE_APPROVAL_GATE_ENABLED) and is_marketplace_management_enabled(kind)


def warn_marketplace_approval_gate_enabled() -> None:
    """Emit a WARNING at startup when the approval gate is armed but has no moderator.

    When the gate is armed (`is_approval_gate_armed()`) and no user account with
    moderation rights exists, every assistant publish lands in PENDING_REVIEW with
    no reviewer able to approve it. Called from the application lifespan, after the
    database is ready, so the operator sees the situation before traffic arrives.

    A deployment that does not yet have a moderator account should set
    MARKETPLACE_APPROVAL_GATE_ENABLED=false until one is configured, or
    assistants will queue indefinitely.
    """
    if config.MARKETPLACE_APPROVAL_GATE_ENABLED and not config.MARKETPLACE_MANAGEMENT_ENABLED:
        _logger.warning(
            "MARKETPLACE_APPROVAL_GATE_ENABLED=True is ignored because "
            "MARKETPLACE_MANAGEMENT_ENABLED=False: there is no route to approve a submission, "
            "so the approval gate stays off and publishing self-approves."
        )
        return
    if not is_approval_gate_armed():
        return
    try:
        from codemie.service.user.user_repository import user_repository
        from codemie.clients.postgres import PostgresClient
        import sqlmodel

        with sqlmodel.Session(PostgresClient.get_engine()) as session:
            has_moderator = user_repository.has_any_moderator(session)
    except Exception:
        # If we cannot check (DB not yet migrated, import error, etc.), log the
        # warning anyway — the operator is better off seeing a spurious notice
        # than missing a real configuration gap.
        has_moderator = False

    if not has_moderator:
        _logger.warning(
            "MARKETPLACE_APPROVAL_GATE_ENABLED=True but no moderator account was found. "
            "Assistants published to the marketplace will enter PENDING_REVIEW and cannot "
            "be approved until a moderator account is configured. "
            "Set MARKETPLACE_APPROVAL_GATE_ENABLED=false to restore the previous "
            "self-approve behaviour while the moderator account is set up."
        )
