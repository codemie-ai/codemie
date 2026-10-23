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

"""Responses of the Usage Attribution endpoints: settings, ranges, fast totals, views of the cached analysis, popups.

Display settings (candidate thresholds, highlights) are applied here, when a response is built, so changing them
never recomputes the analysis. Thresholds are per 30 days and scale with the selected range.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter, OrderedDict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.usage_attribution import app_db, funding, parsing, queries
from codemie.service.usage_attribution.analysis import (
    AnalysisHandle,
    AnalysisService,
    RangeInfo,
    get_analysis_service,
    resolve_range,
)
from codemie.service.usage_attribution.constants import (
    CACHE_NAMESPACE,
    CHARGED,
    DEFAULT_FRAMEWORK_SKILL_REGEX,
    FEATURE_KEY,
    PERSONAL,
    SECTION_BREAKDOWN,
    SECTION_GROUP_DETAIL,
    SECTION_GROUPS,
    SECTION_PEOPLE,
    SECTION_PEOPLE_WORK,
    SECTION_PERSON_DETAIL,
    SECTION_SHARED,
    SECTION_SHARED_DETAIL,
    SECTION_SUMMARY,
    SHARED,
    WEB_KIND_BY_METRIC,
)
from codemie.service.usage_attribution.people_groups import candidate_ids, group_candidates, median_days
from codemie.service.usage_attribution.sections import r2, shard_of
from codemie.service.usage_attribution.settings import (
    DefaultSettings,
    EffectiveSettings,
    build_defaults,
    build_effective,
    decode_overrides,
    get_path,
    schema_payload,
    stable_hash,
    user_can_tune,
    validate_overrides,
)

logger = logging.getLogger(__name__)

SHARED_SORTS = ("name", "ncb", "platform", "cli", "cli_sh", "people", "prod_pct", "via", "share")
GROUP_SORTS = ("name", "grouped", "repositories", "people", "ncb", "spend", "prod_pct", "share")
PEOPLE_SORTS = (
    "name",
    "total",
    "ncb",
    "platform",
    "prod",
    "exp",
    "uncl",
    "cli",
    "projects",
    "share",
    "charged_back",
    "prod_ncb",
    "personal_learning",
    "personal_other",
    "production_days",
)
PEOPLE_FILTERS = ("all", "cand", "prod", "exp", "shared", "platform")
PEOPLE_GROUP_SORTS = ("label", "amount", "estimated", "candidates", "median_days")
_TOTALS_TTL_S = 15 * 60


class TuningForbiddenError(Exception):
    """The caller may not send setting overrides (auditors, or tuning switched off)."""


class EntityNotFoundError(Exception):
    pass


class FeatureUnavailableError(Exception):
    """A part of the tab that needs another feature, such as user enrichment."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------------------------------
_defaults_cache: dict[str, Any] = {"key": None, "value": None}


def component_settings() -> dict[str, Any]:
    """The feature's customer configuration, with the deployment's environment overrides (validated with it)."""
    from codemie.configs.config import config
    from codemie.configs.customer_config import customer_config

    component = customer_config.resolve_component(f"features:{FEATURE_KEY}")
    settings = component.settings.model_dump() if component is not None else {}
    if config.USAGE_ATTRIBUTION_ES_CONCURRENCY is not None:
        settings["elasticsearchConcurrency"] = config.USAGE_ATTRIBUTION_ES_CONCURRENCY
    if config.USAGE_ATTRIBUTION_ANALYSIS_TIMEOUT_SECONDS is not None:
        settings["analysisTimeoutSeconds"] = config.USAGE_ATTRIBUTION_ANALYSIS_TIMEOUT_SECONDS
    return settings


def load_defaults() -> DefaultSettings:
    """Defaults from the code and the customer configuration (rebuilt when the configuration changes)."""
    raw = component_settings()
    key = stable_hash(raw)
    if _defaults_cache["key"] != key:
        _defaults_cache.update(key=key, value=build_defaults(raw))
    return _defaults_cache["value"]


def framework_regex() -> str:
    """The delivery-framework skills as one Lucene regexp, from features:cliAnalytics.deliveryFramework."""
    from codemie.configs.customer_config import customer_config

    raw = customer_config.get_feature_setting("cliAnalytics", "deliveryFramework", default=None)
    rules = (raw or {}).get("rules") if isinstance(raw, dict) else None
    parts = []
    for rule in rules or []:
        regex = rule.get("regex") if isinstance(rule, dict) else None
        if not isinstance(regex, str) or not regex:
            continue
        body = regex[1:] if regex.startswith("^") else regex
        if any(token in body for token in ("(?", "\\d", "\\w", "\\s", "$", "\\b")):
            continue  # not portable to Lucene regexp
        parts.append(body)
    return f"({'|'.join(parts)}).*" if parts else DEFAULT_FRAMEWORK_SKILL_REGEX


def user_enrichment_enabled() -> bool:
    """Country, city, job title group and full names come from user enrichment, which has its own feature flag."""
    from codemie.configs.customer_config import customer_config

    return customer_config.is_feature_enabled("userEnrichmentEnabled")


def effective_settings(user: Any, overrides_raw: str | None) -> EffectiveSettings:
    """Validated overrides on the defaults. Raises SettingsError (422) or TuningForbiddenError (403)."""
    defaults = load_defaults()
    payload = decode_overrides(overrides_raw, int(defaults.values["tuning"]["maxOverrideBytes"]))
    if payload and not user_can_tune(user, defaults):
        raise TuningForbiddenError
    overrides = validate_overrides(payload, defaults)
    # Both change what is read, so both are part of the inputs key.
    extra = {"framework": framework_regex(), "enrichment": user_enrichment_enabled()}
    return build_effective(defaults, overrides, extra_fetch=extra)


def range_for(
    eff: EffectiveSettings, time_period: str | None, start_date: datetime | None, end_date: datetime | None
) -> RangeInfo:
    return resolve_range(
        time_period,
        start_date,
        end_date,
        int(eff.values["maxRangeDays"]),
        int(eff.values["minimumEvidenceDays"]),
    )


# ---------------------------------------------------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------------------------------------------------
def sort_rows(
    rows: list[dict], sort_by: str | None, sort_order: str, allowed: tuple[str, ...], default: str
) -> list[dict]:
    key = sort_by if sort_by in allowed else default
    desc = sort_order != "asc"

    def value(row: dict) -> Any:
        v = row.get(key)
        if isinstance(v, list):
            v = len(v) * 10 + (1 if "cli" in v else 0)
        return v

    present = [r for r in rows if value(r) is not None]
    missing = [r for r in rows if value(r) is None]
    if present and isinstance(value(present[0]), str):
        present.sort(key=lambda r: str(value(r)).lower(), reverse=desc)
    else:
        present.sort(key=lambda r: float(value(r) or 0), reverse=desc)
    return present + missing


_GROUPED_MAX = 16
_grouped_memo: OrderedDict[tuple[str, int, str], tuple[list, list[dict], dict]] = OrderedDict()


def _grouped(
    handle: AnalysisHandle, work: list[dict], candidates: set[str], days: int, group_by: str
) -> tuple[list[dict], dict[str, Any]]:
    """Candidates grouped by ``group_by``, built once per analysis, candidate days and grouping: the summary, the
    groups, every group and every person popup ask for the same. An entry holds the decoded production work it was
    built from and serves only that same object, so a re-read analysis is grouped again."""
    key = (f"{handle.prefix}:{handle.meta.get('computed_at')}", days, group_by)
    hit = _grouped_memo.get(key)
    if hit is not None and hit[0] is work:
        _grouped_memo.move_to_end(key)
        return hit[1], hit[2]
    groups, none = group_candidates(work, candidates, group_by)
    _grouped_memo[key] = (work, groups, none)
    while len(_grouped_memo) > _GROUPED_MAX:
        _grouped_memo.popitem(last=False)
    return groups, none


def candidate_days(eff: EffectiveSettings, rng: RangeInfo) -> int:
    """Production days that make a chargeback candidate. Days are counted over the evidence window (at least 30 days),
    so the setting scales with that window, rounded half up: 5 for any range up to 30 days, 15 for 90 days."""
    days = eff.get("candidateProductionDaysPer30Days") * rng.evidence_window()["days"] / 30.0
    return max(1, int(days + 0.5))


def paginate(rows: list[dict], page: int, per_page: int) -> tuple[list[dict], dict[str, Any]]:
    total = len(rows)
    start = page * per_page
    return rows[start : start + per_page], {
        "page": page,
        "per_page": per_page,
        "total_count": total,
        "has_more": start + per_page < total,
    }


def _matches(text: str, search: str | None) -> bool:
    return not search or search.strip().lower() in text.lower()


def with_shares(rows: list[dict], spend: Callable[[dict], float], total: float) -> list[dict]:
    """Copies of the rows with ``share``: their spend as a percentage of all spend in the period (Total Spend)."""
    return [dict(r, share=round(spend(r) / total * 100.0, 4) if total > 0 else None) for r in rows]


def share_total(rows: list[dict], total: float) -> float | None:
    """The shares of the rows added up: the part of all spend in the period that they cover."""
    return round(sum(r["share"] for r in rows), 2) if total > 0 else None


def _learning_over(r: dict, exp_thr: float) -> bool:
    """Learning and experiments paid from a personal budget over the highlight: the column People shows."""
    return r["personal_learning"] >= exp_thr and r["personal_learning"] > 0


def _people_predicates(candidates: set[str], exp_thr: float, web_thr: float) -> dict[str, Callable[[dict], bool]]:
    """One predicate per People filter flag, shared by the row filter and by the filter counts."""
    return {
        "cand": lambda r: r["uid"] in candidates,
        "prod": lambda r: r["prod_ncb"] > 0.5,
        "exp": lambda r: _learning_over(r, exp_thr),
        "shared": lambda r: r["shared_no_budget"] > 0.5,
        "platform": lambda r: r["platform"] >= web_thr and r["platform"] > 0,
    }


# ---------------------------------------------------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------------------------------------------------
class UsageAttributionHandler:
    def __init__(self, user: Any, service: AnalysisService | None = None):
        self.user = user
        self.service = service or get_analysis_service()

    # ------------------------------------------------------------------ analysis
    async def _analysis(self, rng: RangeInfo, eff: EffectiveSettings) -> AnalysisHandle:
        job_extra = {
            "framework_regex": eff.extra_fetch.get("framework") or framework_regex(),
            "user_enrichment": bool(eff.extra_fetch.get("enrichment", True)),
        }
        return await self.service.get_analysis(rng, eff, str(self.user.id), job_extra)

    def analysis_block(self, handle: AnalysisHandle, eff: EffectiveSettings) -> dict[str, Any]:
        meta = handle.meta
        headline = meta.get("headline") or {}
        return {
            "computed_at": meta.get("computed_at"),
            "duration_ms": meta.get("duration_ms"),
            "cache": handle.cache,
            "stale": handle.cache == "stale",
            # A newer analysis is being computed: the page asks again shortly.
            "refreshing": bool(handle.extra.get("refreshing")),
            "error": handle.extra.get("error"),
            "ttl_s": meta.get("ttl_s"),
            "window": meta.get("window"),
            "evidence_window": meta.get("evidence_window"),
            "coverage": headline.get("coverage"),
            "unclassified_reasons": headline.get("unclassified"),
            "allocated": headline.get("allocated"),
            "timings": {
                "total_ms": meta.get("total_ms"),
                "es_ms": meta.get("es_ms"),
                "db_ms": meta.get("db_ms"),
                "analysis_ms": meta.get("analysis_ms"),
                "sections_ms": meta.get("sections_ms"),
                "requests": meta.get("requests"),
                "inputs_cached": meta.get("inputs_cached"),
                "peak_rss_mb": meta.get("peak_rss_mb"),
            },
            "settings": eff.info(),
        }

    @staticmethod
    def display(eff: EffectiveSettings, rng: RangeInfo) -> dict[str, float]:
        scale = rng.scale
        return {
            "shared_project_threshold": round(eff.get("sharedProjectThresholdPer30Days") * scale, 2),
            "repository_group_threshold": round(eff.get("repositoryGroupThresholdPer30Days") * scale, 2),
            "experiments_highlight": round(eff.get("experimentsHighlightPer30Days") * scale, 2),
            "platform_people_filter": round(eff.get("platformPeopleFilterPer30Days") * scale, 2),
            "candidate_production_days": candidate_days(eff, rng),
            "scale": round(scale, 4),
        }

    # ------------------------------------------------------------------ fast totals
    async def totals(self, rng: RangeInfo) -> dict[str, Any]:
        """Total, Web Platform, CLI, Charged Back and Who Pays, without the analysis (A1)."""
        facts = await app_db.project_facts()
        shared = facts["shared"]
        fplan = funding.plan(funding.funded_intervals(facts["funding"], rng.start, rng.end), rng.start, rng.end)
        key = (
            f"{CACHE_NAMESPACE}:totals:{int(rng.start.timestamp())}:{int(rng.end.timestamp())}:"
            f"{stable_hash([shared, fplan])}"
        )
        cached = await self.service.cache.get(key)
        if cached is not None:
            data = json.loads(cached)
            data["cache"] = "hit"
            return data
        from codemie.repository.metrics_elastic_repository import MetricsElasticRepository

        repo = MetricsElasticRepository()
        t0 = time.perf_counter()
        totals_resp, web_resp = await _gather(
            _query(repo, queries.totals_body(rng.start, rng.end, shared)),
            _query(repo, queries.web_pay_body(rng.start, rng.end, shared, fplan)),
        )
        totals = parsing.parse_totals(totals_resp.get("aggregations"))
        web_aggs = web_resp.get("aggregations") or {}
        web_pay = _web_who_pays(web_aggs, fplan)
        cli = totals["cli"]
        cli_billed = cli["charged_back"] + cli["shared_no_budget"] + cli["personal"]
        web_total = sum(totals["web"].values())
        # Records without a project are personal; the split covers the whole Web Platform total.
        web_pay[PERSONAL] += max(0.0, web_total - sum(web_pay.values()))
        data = {
            "total": r2(cli_billed + web_total),
            "web": {"total": r2(web_total), **{k: r2(v) for k, v in totals["web"].items()}},
            "cli": {"total": r2(cli_billed)},
            "charged_back": r2(cli["charged_back"] + web_pay[CHARGED]),
            "who_pays": {
                "web": {k: r2(v) for k, v in web_pay.items()},
                "cli": {
                    CHARGED: r2(cli["charged_back"]),
                    SHARED: r2(cli["shared_no_budget"]),
                    PERSONAL: r2(cli["personal"]),
                },
            },
            "excluded": {"no_customer": r2(cli["no_customer"]), "other_proxy": r2(totals["other_proxy"])},
            "duration_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "cache": "miss",
        }
        await self.service.cache.set_many({key: json.dumps(data).encode("utf-8")}, _TOTALS_TTL_S)
        return data

    # ------------------------------------------------------------------ analysis views
    async def cli_summary(self, rng: RangeInfo, eff: EffectiveSettings) -> dict[str, Any]:
        handle = await self._analysis(rng, eff)
        summary = (await self.service.load_sections(handle, [SECTION_SUMMARY]))[SECTION_SUMMARY]
        return {
            "tiles": summary["tiles"],
            "pie": summary["pie"],
            "who_pays": summary["who_pays"],
            "categories": summary["categories"],
            "reasons": summary["reasons"],
            "coverage": summary["coverage"],
            "display": self.display(eff, rng),
            "analysis": self.analysis_block(handle, eff),
        }

    async def shared_projects(
        self,
        rng: RangeInfo,
        eff: EffectiveSettings,
        scope: str,
        search: str | None,
        page: int,
        per_page: int,
        sort_by: str | None,
        sort_order: str,
    ) -> dict[str, Any]:
        handle = await self._analysis(rng, eff)
        sections = await self.service.load_sections(handle, [SECTION_SHARED, SECTION_SUMMARY])
        rows, spend_total = sections[SECTION_SHARED], sections[SECTION_SUMMARY]["tiles"]["total"]
        threshold = self.display(eff, rng)["shared_project_threshold"]
        over = [r for r in rows if r["ncb"] >= threshold]
        base = over if scope != "all" else rows
        # A project's spend is its Web Platform and CLI spend.
        matched = with_shares(
            [r for r in base if _matches(r["name"], search)], lambda r: r["platform"] + r["cli"], spend_total
        )
        ordered = sort_rows(matched, sort_by, sort_order, SHARED_SORTS, "ncb")
        page_rows, pagination = paginate(ordered, page, per_page)
        totals = {k: r2(sum(r[k] for r in matched)) for k in ("ncb", "platform", "cli", "cli_sh", "web_sh")}
        totals["share"] = share_total(matched, spend_total)
        return {
            "rows": [dict(r, candidate=r["ncb"] >= threshold) for r in page_rows],
            "totals": totals,
            "pagination": pagination,
            "counts": {"threshold": len(over), "all": len(rows)},
            "threshold": threshold,
            "display": self.display(eff, rng),
            "analysis": self.analysis_block(handle, eff),
        }

    async def repository_groups(
        self,
        rng: RangeInfo,
        eff: EffectiveSettings,
        scope: str,
        search: str | None,
        page: int,
        per_page: int,
        sort_by: str | None,
        sort_order: str,
    ) -> dict[str, Any]:
        handle = await self._analysis(rng, eff)
        sections = await self.service.load_sections(handle, [SECTION_GROUPS, SECTION_SUMMARY])
        rows, spend_total = sections[SECTION_GROUPS], sections[SECTION_SUMMARY]["tiles"]["total"]
        threshold = self.display(eff, rng)["repository_group_threshold"]
        # Excluded repositories (public open-source work) are left out of the candidates and of the groups.
        listed = [r for r in rows if not r["excluded"]]
        candidates = [r for r in listed if r["ncb"] >= threshold]
        multi = [r for r in listed if r["repositories"] > 1 or r["ncb"] >= threshold]
        base = candidates if scope != "all" else multi
        matched = with_shares(
            [r for r in base if _matches(r.get("search") or r["name"], search)], lambda r: r["spend"], spend_total
        )
        ordered = sort_rows(matched, sort_by, sort_order, GROUP_SORTS, "ncb")
        page_rows, pagination = paginate(ordered, page, per_page)
        out_rows = []
        for r in page_rows:
            row = {k: v for k, v in r.items() if k != "search"}
            row["candidate"] = r["ncb"] >= threshold and not r["excluded"]
            out_rows.append(row)
        return {
            "rows": out_rows,
            "totals": {
                **{k: r2(sum(r[k] for r in matched)) for k in ("ncb", "spend")},
                "share": share_total(matched, spend_total),
            },
            "pagination": pagination,
            "counts": {"threshold": len(candidates), "all": len(multi)},
            "threshold": threshold,
            "display": self.display(eff, rng),
            "analysis": self.analysis_block(handle, eff),
        }

    async def breakdown(self, rng: RangeInfo, eff: EffectiveSettings, dimension: str, category: str) -> dict[str, Any]:
        if not eff.extra_fetch.get("enrichment", True):
            raise FeatureUnavailableError(
                "Spend by country and job title group needs user enrichment (features:userEnrichmentEnabled)."
            )
        handle = await self._analysis(rng, eff)
        data = (await self.service.load_sections(handle, [SECTION_BREAKDOWN]))[SECTION_BREAKDOWN]
        rows = (data.get(dimension) or {}).get(category) or []
        return {
            "dimension": dimension,
            "category": category,
            "rows": rows,
            "total": r2(sum(r["spend"] for r in rows)),
            "analysis": self.analysis_block(handle, eff),
        }

    async def people(
        self,
        rng: RangeInfo,
        eff: EffectiveSettings,
        flag: str,
        search: str | None,
        page: int,
        per_page: int,
        sort_by: str | None,
        sort_order: str,
    ) -> dict[str, Any]:
        handle = await self._analysis(rng, eff)
        sections = await self.service.load_sections(handle, [SECTION_PEOPLE, SECTION_SUMMARY])
        rows, spend_total = sections[SECTION_PEOPLE], sections[SECTION_SUMMARY]["tiles"]["total"]
        display = self.display(eff, rng)
        exp_thr, web_thr = display["experiments_highlight"], display["platform_people_filter"]
        candidates = candidate_ids(rows, display["candidate_production_days"])
        predicates = _people_predicates(candidates, exp_thr, web_thr)
        keep = predicates.get(flag, lambda _r: True)

        counts = {"all": len(rows), **{name: sum(1 for r in rows if pred(r)) for name, pred in predicates.items()}}
        matched = with_shares(
            [
                r
                for r in rows
                if keep(r) and _matches(" ".join([r["name"], r.get("email") or "", r.get("main_repo") or ""]), search)
            ],
            lambda r: r["total"],
            spend_total,
        )
        ordered = sort_rows(matched, sort_by, sort_order, PEOPLE_SORTS, "total")
        page_rows, pagination = paginate(ordered, page, per_page)
        return {
            "rows": [
                dict(r, exp_highlight=_learning_over(r, exp_thr), candidate=r["uid"] in candidates) for r in page_rows
            ],
            "pagination": pagination,
            "counts": counts,
            "display": display,
            "analysis": self.analysis_block(handle, eff),
        }

    async def _candidates(
        self, rng: RangeInfo, eff: EffectiveSettings, group_by: str
    ) -> tuple[AnalysisHandle, dict[str, Any], list[dict], dict[str, dict], set[str], list[dict], dict[str, Any]]:
        """People rows, chargeback candidates and their groups, built on request: the candidate days are an instant
        display setting (people_groups.py)."""
        handle = await self._analysis(rng, eff)
        sections = await self.service.load_sections(handle, [SECTION_PEOPLE, SECTION_PEOPLE_WORK])
        rows = sections[SECTION_PEOPLE]
        display = self.display(eff, rng)
        days = display["candidate_production_days"]
        candidates = candidate_ids(rows, days)
        groups, none = _grouped(handle, sections[SECTION_PEOPLE_WORK], candidates, days, group_by)
        return handle, display, rows, {r["uid"]: r for r in rows}, candidates, groups, none

    @staticmethod
    def _group_row(g: dict[str, Any], by_uid: dict[str, dict], threshold: float) -> dict[str, Any]:
        return {
            "id": g["id"],
            "condition": g["condition"],
            "name": g["name"],
            "label": g["label"],
            "how": g["how"],
            "candidates": len(g["people"]),
            "repositories": len(g["repositories"]),
            "amount": r2(g["amount"]),
            "estimated": r2(g["estimated"]),
            "median_days": median_days(g["people"], by_uid),
            "over_threshold": g["amount"] >= threshold,
        }

    @staticmethod
    def _none_row(none: dict[str, Any], by_uid: dict[str, dict]) -> dict[str, Any]:
        return {
            "id": "none",
            "candidates": len(none["people"]),
            "amount": r2(none["amount"]),
            "estimated": r2(none["estimated"]),
            "median_days": median_days(none["people"], by_uid),
        }

    async def people_summary(self, rng: RangeInfo, eff: EffectiveSettings) -> dict[str, Any]:
        """People tiles: chargeback candidates, their production on personal budgets, and how much of it they share
        with other candidates (by common condition)."""
        handle, display, rows, _by_uid, candidates, groups, _none = await self._candidates(rng, eff, "common")
        threshold = display["repository_group_threshold"]
        over = [g for g in groups if g["amount"] >= threshold]
        with_production = [r for r in rows if r["prod_ncb"] > 0.5]
        return {
            "tiles": {
                "candidates": len(candidates),
                "people_with_production": len(with_production),
                "candidates_production": r2(sum(r["prod_ncb"] for r in with_production if r["uid"] in candidates)),
                "production": r2(sum(r["prod_ncb"] for r in with_production)),
                "in_groups": r2(sum(g["amount"] for g in groups)),
                "groups": len(groups),
                "groups_over": len(over),
                "groups_over_amount": r2(sum(g["amount"] for g in over)),
            },
            "threshold": threshold,
            "display": display,
            "analysis": self.analysis_block(handle, eff),
        }

    async def people_groups(
        self,
        rng: RangeInfo,
        eff: EffectiveSettings,
        group_by: str,
        min_amount: int,
        search: str | None,
        page: int,
        per_page: int,
        sort_by: str | None,
        sort_order: str,
    ) -> dict[str, Any]:
        """Chargeback candidates grouped by ``group_by``; ``min_amount`` is per 30 days, scaled to the range. The work
        no other candidate shares is the "none" row, listed with every amount."""
        handle, display, _rows, by_uid, _candidates, groups, none = await self._candidates(rng, eff, group_by)
        threshold = display["repository_group_threshold"]

        def hit(g: dict[str, Any]) -> bool:
            if not search or _matches(g["label"] or "", search):
                return True
            people = (by_uid.get(u) for u in g["people"])
            return any(_matches(f"{p['name']} {p.get('email') or ''}", search) for p in people if p)

        minimum = min_amount * rng.scale
        matched = [self._group_row(g, by_uid, threshold) for g in groups if g["amount"] >= minimum and hit(g)]
        ordered = sort_rows(matched, sort_by, sort_order, PEOPLE_GROUP_SORTS, "amount")
        page_rows, pagination = paginate(ordered, page, per_page)
        # The work no other candidate shares is the last row: it comes with the last page.
        show_none = not min_amount and none["people"] and hit(none) and not pagination["has_more"]
        return {
            "rows": page_rows,
            "none": self._none_row(none, by_uid) if show_none else None,
            "totals": {"amount": r2(sum(r["amount"] for r in matched))},
            "pagination": pagination,
            "counts": {"groups": len(groups), "shown": len(matched)},
            "threshold": threshold,
            "display": display,
            "analysis": self.analysis_block(handle, eff),
        }

    async def people_group(
        self, rng: RangeInfo, eff: EffectiveSettings, group_by: str, group_id: str
    ) -> dict[str, Any]:
        """One group of chargeback candidates (or "none"): its candidates, repositories and why they are together."""
        handle, display, _rows, by_uid, _candidates, groups, none = await self._candidates(rng, eff, group_by)
        g = none if group_id == "none" and none["people"] else next((x for x in groups if x["id"] == group_id), None)
        if g is None:
            raise EntityNotFoundError(group_id)
        members = []
        for uid, amount in sorted(g["people"].items(), key=lambda kv: (-kv[1], kv[0])):
            r = by_uid.get(uid) or {}
            members.append(
                {
                    "uid": uid,
                    **{k: r.get(k) for k in ("name", "email", "city", "country", "job", "projects", "main_repo")},
                    "in_group": r2(amount),
                    "estimated": r2(g["people_estimated"].get(uid, 0.0)),
                    "prod_ncb": r.get("prod_ncb", 0.0),
                    "production_days": r.get("production_days", 0),
                    "charged_back": r.get("charged_back", 0.0),
                }
            )
        repositories = sorted(
            (
                {"repository": k, "candidates": len(d["people"]), "amount": r2(d["amount"])}
                for k, d in g["repositories"].items()
            ),
            key=lambda x: (-x["amount"], x["repository"]),
        )
        threshold = display["repository_group_threshold"]
        return {
            "row": self._none_row(g, by_uid) if g is none else self._group_row(g, by_uid, threshold),
            "members": members,
            "repositories": repositories,
            "facts": {
                "condition": g["condition"],
                "how": g["how"],
                "reason": g["reason"],
                "repositories": [x["repository"] for x in repositories],
            },
            "charged_back_same_people": r2(sum((by_uid.get(u) or {}).get("charged_back", 0.0) for u in g["people"])),
            "display": display,
            "analysis": self.analysis_block(handle, eff),
        }

    async def status(self, rng: RangeInfo, eff: EffectiveSettings) -> dict[str, Any]:
        """How It Works: data quality, run details and the settings in effect."""
        handle = await self._analysis(rng, eff)
        summary = (await self.service.load_sections(handle, [SECTION_SUMMARY]))[SECTION_SUMMARY]
        meta = handle.meta
        schema = schema_payload(load_defaults(), False)["settings"]
        return {
            "tiles": {
                "uncl": summary["tiles"]["uncl"],
                "uncl_parts": summary["tiles"]["uncl_parts"],
                "allocated": summary["tiles"]["allocated"],
                "excluded": summary["tiles"]["excluded"],
                "cli": summary["tiles"]["cli"],
            },
            "coverage": summary["coverage"],
            "reasons": summary["reasons"],
            "signals": summary["signals"],
            "identity": summary["identity"],
            "folder_like": summary["folder_like"][:20],
            "confidence": summary["confidence"],
            "db": summary["db"],
            "counts": summary["counts"],
            "run": {
                "requests": meta.get("requests"),
                "es_ms": meta.get("es_ms"),
                "db_ms": meta.get("db_ms"),
                "analysis_ms": meta.get("analysis_ms"),
                "sections_ms": meta.get("sections_ms"),
                "total_ms": meta.get("total_ms"),
                "duration_ms": meta.get("duration_ms"),
                "inputs_cached": meta.get("inputs_cached"),
                "evidence_reused": meta.get("evidence_reused"),
                "web_funding": meta.get("web_funding"),
                "peak_rss_mb": meta.get("peak_rss_mb"),
                "inputs_kb": meta.get("inputs_kb"),
                "sections_kb": meta.get("sections_kb"),
                "max_buckets": meta.get("max_buckets"),
                "es_queries": meta.get("es_queries"),
                "db_queries": meta.get("db_queries"),
                "steps_ms": meta.get("steps_ms"),
                "cache_backend": self.service.cache.backend,
                "fetch_hash": eff.fetch_hash,
                "analysis_hash": eff.analysis_hash,
            },
            "settings": {
                "values": {s["key"]: get_path(eff.values, s["key"]) for s in schema},
                "defaults": {s["key"]: s["default"] for s in schema},
                "overridden": sorted(eff.overrides),
                "signal_points": {k: list(v) for k, v in eff.values["weights"].items()},
                "fixed": {
                    "maxRangeDays": eff.values["maxRangeDays"],
                    "analysisCacheMinutes": eff.values["analysisCacheMinutes"],
                    "analysisTimeoutSeconds": eff.values["analysisTimeoutSeconds"],
                },
            },
            "display": self.display(eff, rng),
            "analysis": self.analysis_block(handle, eff),
        }

    def settings(self) -> dict[str, Any]:
        defaults = load_defaults()
        return schema_payload(
            defaults,
            user_can_tune(self.user, defaults),
            can_refresh=bool(getattr(self.user, "is_admin_or_maintainer", False)),
            enrichment=user_enrichment_enabled(),
        )

    async def refresh(self, rng: RangeInfo, eff: EffectiveSettings) -> dict[str, Any]:
        await self.service.refresh(rng, eff)
        return {"status": "accepted", "message": "The analysis will be recomputed on the next request."}

    # ------------------------------------------------------------------ popups
    async def _detail(
        self, rng: RangeInfo, eff: EffectiveSettings, section: str, entity: str
    ) -> tuple[dict, AnalysisHandle]:
        handle = await self._analysis(rng, eff)
        name = f"{section}:{shard_of(entity)}"
        shard = (await self.service.load_sections(handle, [name]))[name]
        item = shard.get(entity)
        if item is None:
            raise EntityNotFoundError(entity)
        return item, handle

    async def repository_group(self, rng: RangeInfo, eff: EffectiveSettings, group_id: str) -> dict[str, Any]:
        item, handle = await self._detail(rng, eff, SECTION_GROUP_DETAIL, group_id)
        threshold = self.display(eff, rng)["repository_group_threshold"]
        row = dict(item["row"], candidate=item["row"]["ncb"] >= threshold and not item["row"]["excluded"])
        return {"row": row, "detail": item["detail"], "analysis": self.analysis_block(handle, eff)}

    async def shared_project(self, rng: RangeInfo, eff: EffectiveSettings, project: str) -> dict[str, Any]:
        item, handle = await self._detail(rng, eff, SECTION_SHARED_DETAIL, project)
        threshold = self.display(eff, rng)["shared_project_threshold"]
        row = dict(item["row"], candidate=item["row"]["ncb"] >= threshold)
        detail = dict(item["detail"])
        detail.update(await self._project_web(rng, project))
        models: Counter = Counter()
        for m in detail.get("models") or []:
            models[m["model"]] += m["spend"]
        for m in detail.get("web_models") or []:
            models[m["model"]] += m["spend"]
        detail["models_all"] = [{"model": m, "spend": r2(a)} for m, a in models.most_common(6)]
        return {"row": row, "detail": detail, "analysis": self.analysis_block(handle, eff)}

    async def person(self, rng: RangeInfo, eff: EffectiveSettings, uid: str) -> dict[str, Any]:
        item, handle = await self._detail(rng, eff, SECTION_PERSON_DETAIL, uid)
        display = self.display(eff, rng)
        row = dict(
            item["row"], exp_highlight=item["row"]["exp"] >= display["experiments_highlight"] and item["row"]["exp"] > 0
        )
        detail = dict(item["detail"])
        detail.update(await self._person_live(rng, eff, uid, detail))
        detail["chargeback"] = await self._person_chargeback(rng, eff, uid, row)
        return {"row": row, "detail": detail, "analysis": self.analysis_block(handle, eff)}

    async def _person_chargeback(self, rng: RangeInfo, eff: EffectiveSettings, uid: str, row: dict) -> dict[str, Any]:
        """Whether a person is a chargeback candidate, and the groups (by common condition) they share with others."""
        _handle, display, _rows, _by_uid, candidates, groups, none = await self._candidates(rng, eff, "common")
        mine = [
            {
                "id": g["id"],
                "condition": g["condition"],
                "label": g["label"],
                "amount": r2(g["people"][uid]),
                "others": len(g["people"]) - 1,
            }
            for g in groups
            if uid in g["people"]
        ]
        return {
            "candidate": uid in candidates,
            "production_days": row.get("production_days", 0),
            "threshold_days": display["candidate_production_days"],
            "groups": sorted(mine, key=lambda x: (-x["amount"], x["label"] or "")),
            "alone": r2(none["people"].get(uid, 0.0)),
        }

    async def _project_web(self, rng: RangeInfo, project: str) -> dict[str, Any]:
        from codemie.repository.metrics_elastic_repository import MetricsElasticRepository

        resp = await _query(MetricsElasticRepository(), queries.project_web_detail_body(rng.start, rng.end, project))
        aggs = resp.get("aggregations") or {}
        assets: dict[tuple[str, str], dict] = {}
        models: Counter = Counter()
        types: Counter = Counter()
        for side in ("p", "f"):
            self._merge_project_web_side(aggs.get(side) or {}, assets, models, types)
        return {
            "assets": sorted(({**a, "spend": r2(a["spend"])} for a in assets.values()), key=lambda a: -a["spend"])[:25],
            "web_models": [{"model": m, "spend": r2(a)} for m, a in models.most_common(6)],
            "web_types": {k: r2(types.get(k, 0.0)) for k in ("assistant", "workflow", "datasource")},
        }

    @staticmethod
    def _merge_project_web_side(
        b: dict[str, Any], assets: dict[tuple[str, str], dict], models: Counter, types: Counter
    ) -> None:
        """One side ("paid" or "fallback") of a shared project's Web Platform detail, folded into the shared totals."""
        for kind, path in (
            ("Assistant", ("assistants",)),
            ("Workflow", ("workflows",)),
            ("Datasource", ("datasources", "t")),
        ):
            for x in parsing.bucket_list(b, *path):
                UsageAttributionHandler._merge_project_web_asset(assets, kind, x)
        for m in parsing.bucket_list(b, "models"):
            models[str(m["key"])] += parsing.num(m, "spend")
        for t in parsing.bucket_list(b, "types"):
            types[WEB_KIND_BY_METRIC.get(str(t["key"]), str(t["key"]))] += parsing.num(t, "spend")

    @staticmethod
    def _merge_project_web_asset(assets: dict[tuple[str, str], dict], kind: str, x: dict[str, Any]) -> None:
        a = assets.setdefault(
            (kind, str(x["key"])),
            {"name": str(x["key"]), "kind": kind, "invocations": 0, "spend": 0.0, "users": None, "last": None},
        )
        a["invocations"] += int(x.get("doc_count", 0))
        a["spend"] += parsing.num(x, "spend")
        if kind != "Datasource":
            a["users"] = max(a["users"] or 0, int(parsing.num(x, "users")))
        last = (parsing.ts_value(x, "last") or "")[:10] or None
        if last and (a["last"] is None or last > a["last"]):
            a["last"] = last

    async def _person_live(
        self, rng: RangeInfo, eff: EffectiveSettings, uid: str, detail: dict[str, Any]
    ) -> dict[str, Any]:
        from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
        from codemie.service.analytics.delivery_framework import classify_delivery_framework

        repo = MetricsElasticRepository()
        facts = await app_db.project_facts()
        sessionless = list(eff.values.get("sessionlessClients") or [])
        cli_resp, web_resp = await _gather(
            _query(repo, queries.person_cli_detail_body(rng.start, rng.end, uid, sessionless, facts["shared"])),
            _query(repo, queries.person_web_detail_body(rng.start, rng.end, uid)),
        )
        cli = cli_resp.get("aggregations") or {}
        web = web_resp.get("aggregations") or {}
        categories = {r["value"]: r["category"] for r in detail.get("repos") or []}
        recent_buckets = parsing.bucket_list(cli, "recent", "t")
        extra = await self._person_session_extras(repo, rng, recent_buckets)
        return {
            "models": self._person_model_rows(cli, web),
            "agents": self._person_agents(cli, sessionless),
            "recent": self._person_recent_sessions(recent_buckets, categories, extra, classify_delivery_framework),
            "assets": self._person_web_assets(web),
            "active_days": int(parsing.num(cli, "days")),
            "project_sessions": {
                str(p["key"]): int(parsing.num(p, "sessions", "n")) for p in parsing.bucket_list(cli, "projects")
            },
        }

    @staticmethod
    async def _person_session_extras(repo: Any, rng: RangeInfo, recent_buckets: list[dict]) -> dict[str, dict]:
        """Prompt counts and skills for the person's recent sessions, fetched only when there are any."""
        session_ids = [str(b["key"]) for b in recent_buckets]
        if not session_ids:
            return {}
        extra_resp = await _query(repo, queries.sessions_extra_body(rng.start, rng.end, session_ids))
        return {
            str(b["key"]): {
                "prompts": parsing.num(b, "prompts"),
                "skills": [str(s["key"]) for s in parsing.bucket_list(b, "skills")],
            }
            for b in parsing.bucket_list(extra_resp.get("aggregations") or {}, "s")
        }

    @staticmethod
    def _person_recent_sessions(
        recent_buckets: list[dict],
        categories: dict[str, str],
        extra: dict[str, dict],
        classify_delivery_framework: Callable[[list[str]], Any],
    ) -> list[dict]:
        recent = []
        for b in recent_buckets:
            sid = str(b["key"])
            value = parsing.first_key(b, "repo")
            info = extra.get(sid) or {}
            recent.append(
                {
                    "date": (parsing.ts_value(b, "last") or "")[:16].replace("T", " "),
                    "repository": value,
                    "branch": parsing.first_key(b, "branch"),
                    "category": categories.get(value or "", "unclassified"),
                    "client": parsing.first_key(b, "client"),
                    "framework": classify_delivery_framework(info.get("skills") or []),
                    "turns": int(info["prompts"]) if info.get("prompts") else None,
                    "requests": int(b.get("doc_count", 0)),
                    "spend": round(parsing.num(b, "spend"), 2),
                }
            )
        return recent

    @staticmethod
    def _person_model_rows(cli: dict[str, Any], web: dict[str, Any]) -> list[dict]:
        models: dict[str, dict] = {}
        for m in parsing.bucket_list(cli, "models"):
            models.setdefault(str(m["key"]), {"model": str(m["key"]), "cli": 0.0, "web": 0.0})["cli"] += parsing.num(
                m, "spend"
            )
        for m in parsing.bucket_list(web, "models"):
            models.setdefault(str(m["key"]), {"model": str(m["key"]), "cli": 0.0, "web": 0.0})["web"] += parsing.num(
                m, "spend"
            )
        return sorted(
            (
                {**m, "spend": r2(m["cli"] + m["web"]), "cli": r2(m["cli"]), "web": r2(m["web"])}
                for m in models.values()
            ),
            key=lambda m: -m["spend"],
        )[:8]

    @staticmethod
    def _person_agents(cli: dict[str, Any], sessionless: list[str]) -> list[dict]:
        agents = []
        for c in parsing.bucket_list(cli, "clients"):
            client = str(c["key"])
            agents.append(
                {
                    "client": client,
                    "sessions": None if client in sessionless else int(parsing.num(c, "sessionful", "n")),
                    "spend": r2(parsing.num(c, "spend")),
                }
            )
        return agents

    @staticmethod
    def _person_web_assets(web: dict[str, Any]) -> list[dict]:
        assets = []
        for kind, key in (("Assistant", "assistants"), ("Workflow", "workflows")):
            for x in parsing.bucket_list(web, key):
                assets.append(
                    {
                        "name": str(x["key"]),
                        "kind": kind,
                        "project": parsing.first_key(x, "proj"),
                        "invocations": int(x.get("doc_count", 0)),
                        "spend": r2(parsing.num(x, "spend")),
                        "last": (parsing.ts_value(x, "last") or "")[:10] or None,
                    }
                )
        assets.sort(key=lambda a: -a["spend"])
        return assets


def _web_who_pays(aggs: dict[str, Any], fplan: dict[str, Any]) -> dict[str, float]:
    """Web Platform who pays from ``web_pay_body``: fallback records and the part of shared projects that no project
    budget paid were paid personally inside shared projects; the funded part was charged back (funding.py)."""
    shared = parsing.num(aggs, "shared", "spend")
    funded = parsing.parse_web_funding(aggs, fplan)
    charged = min(shared, sum(row[1] for members in funded.values() for row in members.values()))
    return {
        CHARGED: charged,
        SHARED: parsing.num(aggs, "fb", "spend") + shared - charged,
        PERSONAL: parsing.num(aggs, "personal", "spend"),
    }


_QUERY_FAILED = "Usage data could not be read from Elasticsearch. Try again in a few minutes."


async def _query(repo: Any, body: dict[str, Any]) -> dict[str, Any]:
    """An aggregation query whose failure reaches the client without the transport details the repository puts in it
    (host, port, exception names); they stay in the server log."""
    try:
        return await repo.execute_aggregation_query(body)
    except ExtendedHTTPException as e:
        logger.warning("Usage attribution query failed: %s", e.details)
        raise ExtendedHTTPException(code=e.code, message=e.message, details=_QUERY_FAILED) from e


async def _gather(*aws: Any) -> list[Any]:
    return list(await asyncio.gather(*aws))
