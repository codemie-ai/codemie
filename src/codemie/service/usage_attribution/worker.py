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

"""Worker process entry point: reads, analysis and sections, outside the API's event loop.

The API process runs ``run_job`` in a ``spawn`` process of its own for every computation, so the memory is released
afterwards. The job carries everything the computation needs: the windows, the effective settings, and, when they
are cached, the gzipped inputs, so a re-analysis skips Elasticsearch, or the evidence of an earlier computation in
the hour, so only the spend of the period is read. A watchdog stops the process when it goes above
``workerMemoryLimitMb``. Imports stay lean: Elasticsearch client, database session and the analysis modules.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import resource
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from codemie.service.usage_attribution import funding, parsing, queries
from codemie.service.usage_attribution.constants import (
    DETAIL_SHARDS,
    ES_REQUEST_TIMEOUT_S,
    INPUTS_VERSION,
    MAX_BUCKETS_DEFAULT,
    SHARDED_SECTIONS,
    WORKER_MEMORY_EXIT_CODE,
)
from codemie.service.usage_attribution.fetch import Fetcher, Spec

logger = logging.getLogger(__name__)


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _gz(obj: Any) -> bytes:
    return gzip.compress(json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8"), compresslevel=5)


def _gunzip(blob: bytes | None) -> Any:
    return json.loads(gzip.decompress(blob)) if blob else None


def _peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024, 1)


def _es_client(concurrency: int) -> Any:
    from elasticsearch import Elasticsearch

    from codemie.configs.config import config

    return Elasticsearch(
        config.ELASTIC_URL,
        basic_auth=(config.ELASTIC_USERNAME, config.ELASTIC_PASSWORD),
        verify_certs=False,
        ssl_show_warn=False,
        request_timeout=ES_REQUEST_TIMEOUT_S,
        connections_per_node=max(10, concurrency),
        # The fetcher retries transient errors itself, with a pause between attempts (fetch.py).
        max_retries=0,
        retry_on_timeout=False,
    )


def _max_buckets(client: Any) -> int:
    try:
        resp = client.cluster.get_settings(include_defaults=True, filter_path="**.max_buckets")
        data = resp.body if hasattr(resp, "body") else dict(resp)
        for scope in ("transient", "persistent", "defaults"):
            value = ((data.get(scope) or {}).get("search") or {}).get("max_buckets")
            if value:
                return int(value)
    except Exception:  # noqa: BLE001 - the setting is optional; the documented default applies
        pass
    return MAX_BUCKETS_DEFAULT


def _estimates(cards: dict[str, Any]) -> dict[str, int]:
    def card(*path: str) -> int:
        return max(1, int(parsing.num(cards, *path)))

    return {
        "cli_users": card("cli", "users"),
        "cli_repos": card("cli", "repos"),
        "shared_projects": card("cli", "shared", "projects"),
        "tool_users": card("tools", "users"),
        "session_users": card("sessions", "users"),
        "web_users": card("web", "users"),
        "web_projects": card("web", "projects"),
        "fb_projects": card("fb", "projects"),
    }


def _load_app_db(engine: Any, db_timings: list[dict]) -> tuple[dict, Any]:
    from sqlmodel import Session

    from codemie.service.usage_attribution import app_db

    with Session(engine) as session:
        static = app_db.load_static(session, db_timings)
        funding_facts = app_db.load_funding(session, db_timings)
    return static, funding_facts


def _cards_estimate(
    fetcher: Fetcher, ev_from: datetime, ev_to: datetime, shared: Any, evidence: dict | None
) -> tuple[Any, dict]:
    """The A0 sizes spec (run now when there is no reused evidence) and the resulting per-section size estimate."""
    cards_spec = Spec(
        "cards",
        "A0 sizes",
        lambda part, size: queries.cards_body(ev_from, ev_to, shared),
        parse=lambda aggs, acc: aggs,
        new_acc=dict,
    )
    if evidence is None:
        fetcher.execute([cards_spec])
        est = _estimates(cards_spec.result or {})
    else:
        est = evidence["cards"]
    return cards_spec, est


def _money_specs(
    money_from: datetime, money_to: datetime, shared: Any, per_project: int, fplan: Any, est: dict
) -> list[Any]:
    def shared_acc() -> dict:
        return {}

    return [
        Spec(
            "totals",
            "A1 totals and who pays",
            lambda part, size: queries.totals_body(money_from, money_to, shared),
            parse=lambda aggs, acc: parsing.parse_totals(aggs),
            new_acc=dict,
        ),
        Spec(
            "shared_cli",
            "A6 shared projects (CLI)",
            lambda part, size: queries.shared_cli_body(money_from, money_to, shared, part, size, per_project),
            parse=lambda b, acc: parsing.parse_shared_cli(b, False, acc),
            new_acc=shared_acc,
            est=est["shared_projects"],
            per_item=12 + 0.6 * per_project,
            path=("projects",),
        ),
        Spec(
            "shared_cli_fb",
            "A6 shared projects (CLI fallback records)",
            lambda part, size: queries.shared_cli_fb_body(money_from, money_to, shared, part, size, per_project),
            parse=lambda b, acc: parsing.parse_shared_cli(b, True, acc),
            new_acc=shared_acc,
            est=est["fb_projects"],
            per_item=10 + 0.5 * per_project,
            path=("projects",),
        ),
        Spec(
            "web_proj",
            "A7 Web Platform projects",
            lambda part, size: queries.web_projects_body(money_from, money_to, part, size),
            parse=lambda b, acc: parsing.parse_web_projects(b, acc),
            new_acc=dict,
            est=est["web_projects"],
            per_item=5,
            path=("projects",),
        ),
        Spec(
            "web_fb",
            "A7 Web Platform fallback records",
            lambda part, size: queries.web_fb_body(money_from, money_to, part, size),
            parse=lambda b, acc: parsing.parse_web_projects(b, acc),
            new_acc=dict,
            est=est["fb_projects"],
            per_item=5,
            path=("projects",),
        ),
        Spec(
            "web_funding",
            "A9 Web Platform funding",
            lambda part, size: queries.web_funding_body(money_from, money_to, fplan),
            parse=lambda aggs, acc: parsing.parse_web_funding(aggs, fplan),
            new_acc=dict,
        ),
        Spec(
            "web_people",
            "A8 Web Platform people",
            lambda part, size: queries.web_people_body(money_from, money_to, part, size),
            parse=lambda b, acc: parsing.parse_web_people(b, acc),
            new_acc=dict,
            est=est["web_users"],
            per_item=8,
            path=("users",),
        ),
    ]


def _persons_spec(money_from: datetime, money_to: datetime, shared: Any, per_person: int, est: dict) -> Any:
    return Spec(
        "persons",
        "A2 people x repositories",
        lambda part, size: queries.persons_body(money_from, money_to, shared, part, size, per_person),
        parse=lambda b, acc: parsing.parse_persons(b, acc),
        new_acc=dict,
        est=est["cli_users"],
        per_item=8 + 2.5 * per_person,
        path=("users",),
    )


def _evidence_specs(
    ev_from: datetime, ev_to: datetime, shared: Any, per_person: int, framework_regex: str, est: dict, same: bool
) -> list[Any]:
    specs = [
        Spec(
            "values",
            "A3 repository values",
            lambda part, size: queries.values_body(ev_from, ev_to, shared, part, size),
            parse=lambda b, acc: parsing.parse_values(b, acc),
            new_acc=dict,
            est=est["cli_repos"],
            per_item=10,
            path=("repos",),
        ),
        Spec(
            "tool",
            "A4 tool evidence",
            lambda part, size: queries.tools_body(ev_from, ev_to, part, size, per_person),
            parse=lambda b, acc: parsing.parse_tools(b, acc),
            new_acc=list,
            est=est["tool_users"],
            per_item=1 + 1.5 * per_person,
            path=("users",),
        ),
        Spec(
            "sess",
            "A5 session evidence",
            lambda part, size: queries.sessions_body(ev_from, ev_to, framework_regex, part, size, per_person),
            parse=lambda b, acc: parsing.parse_sessions(b, acc),
            new_acc=list,
            est=est["session_users"],
            per_item=1 + 1.5 * per_person,
            path=("users",),
        ),
    ]
    if not same:
        specs.append(
            Spec(
                "ev_persons",
                "A2 people x repositories (evidence window)",
                lambda part, size: queries.persons_body(ev_from, ev_to, shared, part, size, per_person),
                parse=lambda b, acc: parsing.parse_persons(b, acc),
                new_acc=dict,
                est=est["cli_users"],
                per_item=8 + 2.5 * per_person,
                path=("users",),
            )
        )
    return specs


def _merge_shared_cli(by_key: dict) -> dict[str, dict]:
    shared_cli: dict[str, dict] = by_key["shared_cli"].result or {}
    for project, d in (by_key["shared_cli_fb"].result or {}).items():
        # Fallback records of a project merge into the same row as its records without fallback.
        target = shared_cli.setdefault(project, d)
        if target is not d:
            _merge_shared_rows(target, d)
    return shared_cli


def _collect_uids(persons: dict, web_people: dict, shared_cli: dict[str, dict]) -> set[str]:
    uids = set(persons) | set(web_people)
    for d in shared_cli.values():
        uids.update(d["people"])
    return uids


def _build_evidence(
    evidence: dict | None, ev_window: dict, est: dict, by_key: dict, same: bool
) -> tuple[dict, bytes | None]:
    """The reused evidence, or the evidence just read now (returned alongside its gzipped blob to cache)."""
    if evidence is not None:
        return evidence, None
    evidence = {
        "v": INPUTS_VERSION,
        "evidence_window": ev_window,
        "cards": dict(est),
        "values": by_key["values"].result or {},
        "tool": by_key["tool"].result or [],
        "sess": by_key["sess"].result or [],
        "ev_persons": (by_key["persons"].result or {}) if same else (by_key["ev_persons"].result or {}),
    }
    return evidence, _gz(evidence)


def _fetch_meta(
    fetcher: Fetcher,
    max_buckets: int,
    es_ms: float,
    db_ms: float,
    db_timings: list[dict],
    specs: list[Any],
    cards_spec: Any,
    evidence_blob: bytes | None,
    fplan: Any,
    ev_window: dict,
) -> dict[str, Any]:
    es_query_specs = ([cards_spec] if evidence_blob is not None else []) + specs
    return {
        "requests": fetcher.requests,
        "max_buckets": max_buckets,
        "es_ms": round(es_ms, 1),
        "db_ms": round(db_ms, 1),
        "db_queries": db_timings,
        "es_queries": {sp.label: dict(sp.stats, partitions=sp.parts) for sp in es_query_specs},
        # The evidence came from an earlier computation in the hour, with the window it was read for.
        "evidence_reused": evidence_blob is None,
        "web_funding": funding.summary(fplan),
        "evidence_window": ev_window,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def fetch_inputs(job: dict[str, Any]) -> tuple[dict[str, Any], bytes | None]:
    """Read the app DB and Elasticsearch, and return the compact inputs of the analysis, with the evidence (sizes,
    repository values, tools, sessions and people over the evidence window) gzipped apart when it was read now. With
    ``job["evidence_blob"]`` that part is not read again: it comes from an earlier computation in the hour, with the
    window it was read for, so this one reads only its own spend."""
    from codemie.clients.postgres import PostgresClient
    from codemie.configs.config import config
    from sqlmodel import Session

    from codemie.service.usage_attribution import app_db

    settings = job["settings"]
    money_from, money_to = _dt(job["window"]["from"]), _dt(job["window"]["to"])
    ev_from, ev_to = _dt(job["evidence_window"]["from"]), _dt(job["evidence_window"]["to"])
    evidence = _gunzip(job.get("evidence_blob"))
    ev_window = evidence["evidence_window"] if evidence is not None else job["evidence_window"]
    # Reused evidence usually ends before this period: its people are then not this period's.
    same = ev_window == job["window"]
    per_person = int(settings["repositoriesPerPerson"])
    per_project = int(settings["repositoriesPerSharedProject"])
    framework_regex = job["framework_regex"]
    db_timings: list[dict] = []
    t0 = time.perf_counter()
    engine = PostgresClient.get_engine()
    static, funding_facts = _load_app_db(engine, db_timings)
    shared = static["shared"]
    db_ms = (time.perf_counter() - t0) * 1000.0
    # When each shared project's platform budget paid for each member: Web Platform records carry no customer.
    fplan = funding.plan(funding.funded_intervals(funding_facts, money_from, money_to), money_from, money_to)

    concurrency = int(settings["elasticsearchConcurrency"])
    client = _es_client(concurrency)
    t_es = time.perf_counter()
    max_buckets = _max_buckets(client)
    fetcher = Fetcher(
        client,
        config.ELASTIC_METRICS_INDEX,
        concurrency,
        float(settings["partitionBucketShare"]),
        max_buckets,
        ES_REQUEST_TIMEOUT_S,
    )
    cards_spec, est = _cards_estimate(fetcher, ev_from, ev_to, shared, evidence)

    persons_spec = _persons_spec(money_from, money_to, shared, per_person, est)
    money_specs = _money_specs(money_from, money_to, shared, per_project, fplan, est)
    evidence_specs = _evidence_specs(ev_from, ev_to, shared, per_person, framework_regex, est, same)

    # The money-window people are the evidence-window people when both windows are the same.
    persons_shared = evidence is not None and same
    specs = money_specs + ([] if persons_shared else [persons_spec]) + (evidence_specs if evidence is None else [])
    fetcher.plan(specs)
    fetcher.execute(specs)
    es_ms = (time.perf_counter() - t_es) * 1000.0
    by_key = {sp.key: sp for sp in specs}
    evidence, evidence_blob = _build_evidence(evidence, ev_window, est, by_key, same)
    shared_cli = _merge_shared_cli(by_key)
    persons = evidence["ev_persons"] if persons_shared else (by_key["persons"].result or {})
    web_people = by_key["web_people"].result or {}
    uids = _collect_uids(persons, web_people, shared_cli)
    t_people = time.perf_counter()
    with Session(engine) as session:
        people = app_db.load_people(session, sorted(uids), db_timings, job.get("user_enrichment", True))
    db_ms += (time.perf_counter() - t_people) * 1000.0
    inputs = {
        "v": INPUTS_VERSION,
        "window": job["window"],
        "evidence_window": ev_window,
        "persons": persons,
        "ev_persons": None if same else evidence["ev_persons"],
        "values": evidence["values"],
        "tool": evidence["tool"],
        "sess": evidence["sess"],
        "web_proj": by_key["web_proj"].result or {},
        "web_fb": by_key["web_fb"].result or {},
        "web_people": web_people,
        "web_funding": by_key["web_funding"].result or {},
        "shared_cli": shared_cli,
        "totals": by_key["totals"].result or {},
        "cards": dict(est),
        "app_db": dict(static, people=people),
        "meta": _fetch_meta(
            fetcher, max_buckets, es_ms, db_ms, db_timings, specs, cards_spec, evidence_blob, fplan, ev_window
        ),
    }
    return inputs, evidence_blob


def _merge_shared_rows(target: dict, d: dict) -> None:
    """Merge one shared-project row parsed from fallback records into the row of records without fallback."""
    for fld in ("spend", "cb", "sh", "fb"):
        target[fld] += d[fld]
    target["people_n"] = max(target["people_n"], d["people_n"])
    for value, (spend, sh) in d["repos"].items():
        rr = target["repos"].setdefault(value, [0.0, 0.0])
        rr[0] += spend
        rr[1] += sh
    for uid, (spend, name) in d["people"].items():
        pp = target["people"].setdefault(uid, [0.0, name])
        pp[0] += spend
    for client, (spend, sessions) in d["clients"].items():
        cc = target["clients"].setdefault(client, [0.0, 0.0])
        cc[0] += spend
        cc[1] += sessions
    for model, spend in d["models"].items():
        target["models"][model] = target["models"].get(model, 0.0) + spend


def _start_memory_watchdog(limit_mb: float) -> None:
    """Stop this process (exit code WORKER_MEMORY_EXIT_CODE) once its peak memory goes above the limit."""

    def watch() -> None:
        while True:
            time.sleep(0.25)
            if _peak_rss_mb() > limit_mb:
                logger.error("usage attribution worker: memory above %s MB, stopping the computation", limit_mb)
                os._exit(WORKER_MEMORY_EXIT_CODE)

    threading.Thread(target=watch, name="usage-attribution-memory", daemon=True).start()


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    """Compute one analysis. Returns gzipped sections, and the gzipped inputs and evidence when they were read
    now."""
    from codemie.service.usage_attribution.pipeline import Analysis
    from codemie.service.usage_attribution.sections import build_sections

    limit_mb = job["settings"].get("workerMemoryLimitMb")
    if limit_mb:
        _start_memory_watchdog(float(limit_mb))
    t0 = time.perf_counter()
    inputs_blob: bytes | None = job.get("inputs_blob")
    new_inputs = None
    evidence_blob = None
    if inputs_blob:
        inputs = json.loads(gzip.decompress(inputs_blob))
        inputs_cached = True
    else:
        inputs, evidence_blob = fetch_inputs(job)
        new_inputs = _gz(inputs)
        inputs_cached = False
    t_analysis = time.perf_counter()
    an = Analysis(inputs, job["settings"]).run()
    t_sections = time.perf_counter()
    sections = build_sections(an)
    encoded: dict[str, bytes] = {}
    for name, value in sections.items():
        if name in SHARDED_SECTIONS:
            # Every shard is written, empty ones too: a missing shard would read as an expired analysis, and the
            # last good copy would keep entities of an older computation.
            for shard in range(DETAIL_SHARDS):
                encoded[f"{name}:{shard}"] = _gz(value.get(shard) or {})
        else:
            encoded[name] = _gz(value)
    t_end = time.perf_counter()
    meta = dict(inputs.get("meta") or {})
    summary = sections["summary"]
    meta["headline"] = {
        "coverage": summary["coverage"],
        "unclassified": summary["tiles"]["uncl_parts"],
        "allocated": summary["tiles"]["allocated"],
        "counts": summary["counts"],
    }
    meta.update(
        {
            "inputs_cached": inputs_cached,
            "analysis_ms": round((t_sections - t_analysis) * 1000.0, 1),
            "sections_ms": round((t_end - t_sections) * 1000.0, 1),
            "fetch_ms": round((t_analysis - t0) * 1000.0, 1),
            "total_ms": round((t_end - t0) * 1000.0, 1),
            "steps_ms": dict(an.timing),
            "peak_rss_mb": _peak_rss_mb(),
            "inputs_kb": round(len(new_inputs or inputs_blob or b"") / 1024.0, 1),
            "sections_kb": round(sum(len(b) for b in encoded.values()) / 1024.0, 1),
            "pid": os.getpid(),
        }
    )
    return {"sections": encoded, "inputs_blob": new_inputs, "evidence_blob": evidence_blob, "meta": meta}
