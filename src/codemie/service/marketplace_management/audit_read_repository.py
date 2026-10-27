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

"""SQL read path for the Marketplace Management admin views.

EXECUTION-PLAN 5.4; traces F-61, F-07, F-33. Query shapes are
`design-persistence.md` §4.2 point 4 (pin the run once per request) and §5.3
(facets are a `GROUP BY`, decisively).

Why this module exists at all
-----------------------------
Every read used to be served from an in-process `AuditResult`: the router asked
`AUDIT_CACHE.get_or_run(kind)` for the whole catalogue, filtered it with a
Python loop and sliced the list (`marketplace_management.py:470-580`). Three
consequences, all observed:

* **The page cost was the catalogue, not the page.** `?page_size=10` still
  materialised every verdict, every record and every row dict (F-61).
* **A restart emptied it.** The rehydrator rebuilt `Verdict`s without their
  findings or similarity, so a cold `GET /cleanup?reason=invalid` matched
  nothing while `mm_audit_verdict.cleanup_reason` held 49 rows for that very run,
  and every row's `critical_count` read 0 (F-07, F-30).
* **Two readers could disagree.** A re-audit landing between the verdict scan
  and the record scan mixed two runs into one response.

Everything here is pinned to one `audit_run_id`, resolved once per request by
`latest_succeeded_run_id`, and every statement is bounded: aggregates return one
row per distinct value, pages return at most `page_size` rows.

Scope note (`schema_version >= 2`)
----------------------------------
`latest_succeeded_run_id` deliberately ignores `schema_version = 1` runs.
`n1a2u3d4i5t6` stamps every pre-M1 run as v1, and those runs carry no findings
at all — serving an Overview from one would report "no failed checks" for a
catalogue that had failed hundreds (design-persistence.md §6.2).

Catalogue scope
---------------
`_CATALOGUE_SCOPE` is the same predicate `AssistantAdapter.list_records` applies
(`entity_adapter.py:210-225`). The MCP and category facets are counted over the
catalogue rather than over the run, so they stay populated before the first
audit — exactly as the in-memory path behaved.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Optional

from sqlalchemy import text

from codemie.service.marketplace_management.models import CleanupOrderBy

# Published (`is_global`) or moving through the approval workflow. Mirrors
# `AssistantAdapter.list_records`; if that predicate changes, this must too, and
# `test_catalogue_facet_scope_matches_the_adapter` is what says so.
#
# `APPROVED` is not a membership condition (EXECUTION-PLAN 7.2, F-20): it is the
# column's server default, so it says nothing about whether a row was ever
# published. `is_global` is what says that.
_CATALOGUE_SCOPE = "(a.is_global IS TRUE OR a.marketplace_status IN ('PENDING_REVIEW', 'REJECTED'))"

# The MCP identifier `entity_adapter._extract_mcp_ids` derives from each element
# of the `mcp_servers` JSONB array, expressed in SQL so the facet the dropdown
# offers and the filter the table applies are the same string.
_MCP_ID = "COALESCE(e->>'id', e->>'name', e->>'server_name', e->>'url')"

_BUCKET_SKELETON = {"VERIFIED": 0, "COMMUNITY": 0, "DELETE": 0}

# The one OPTIONAL gate (`gates.py`); everything else is CRITICAL. Used only to
# label a rule facet when no finding row carries a severity, which cannot happen
# for a v2 run and is here so a malformed row degrades instead of raising.
_OPTIONAL_RULE = "description_not_equal_name"


def _rows(session, sql: str, **params: Any) -> list:
    return session.execute(text(sql), params).mappings().all()


def _scalar(session, sql: str, **params: Any) -> Any:
    return session.execute(text(sql), params).scalar()


def latest_succeeded_run_id(session, entity_kind: str) -> Optional[str]:
    """The newest run whose data a read may be served from.

    One row, one index scan: `ix_mm_audit_run_kind_started` is `(entity_kind,
    started_at DESC)` and the newest run of a kind is almost always serveable,
    so the status filter is satisfied on the first row read. Resolved once per
    request and threaded through every query below, so a re-audit landing
    mid-page cannot mix two runs into one response (§4.2 point 4).

    `PARTIAL` is serveable on purpose. It means the similarity backend was
    unavailable (§7 F2); the gate verdicts the run wrote are complete and
    correct. Pinning reads to SUCCEEDED only would make an honest degradation
    label empty the Overview, which is a worse failure than the one being
    reported.
    """
    return _scalar(
        session,
        """
        SELECT id FROM mm_audit_run
         WHERE entity_kind = :kind
           AND status IN ('SUCCEEDED', 'PARTIAL')
           AND schema_version >= 2
         ORDER BY started_at DESC
         LIMIT 1
        """,
        kind=entity_kind,
    )


def run_header(session, run_id: str) -> Optional[dict]:
    """Totals and aggregates carried on the run row itself."""
    rows = _rows(
        session,
        """
        SELECT id, total_records, engine_took_seconds, started_at, finished_at, aggregates
          FROM mm_audit_run WHERE id = :run
        """,
        run=run_id,
    )
    return dict(rows[0]) if rows else None


# ---------------------------------------------------------------------------
# Filters — shared by the cleanup page and its count
# ---------------------------------------------------------------------------


def _cleanup_filters(
    *,
    bucket: Optional[str],
    reason: Optional[str],
    tier: Optional[str],
    verdict: Optional[str],
    mcp: Optional[str],
    category: Optional[str],
    cluster_id: Optional[int],
    q: Optional[str],
    rule: Optional[str],
) -> tuple[str, dict]:
    # Only entities that are in the marketplace *now*. Withdraw, reject,
    # re-submit after an edit (PENDING_REVIEW, private) and rollback (DRAFT,
    # private) all mutate the live `assistants` row, not this run's
    # `mm_audit_verdict` snapshot, so without this the Cleanup queue would keep
    # listing an assistant that has left the marketplace as a DELETE candidate
    # until the next audit run (MR !4070 review comment #24). `is_global` is
    # what says an assistant is published (see `_CATALOGUE_SCOPE`), so this
    # filters on it instead of enumerating the statuses that happen to unpublish.
    # A verdict whose assistant row is gone (LEFT JOIN -> NULL) is dropped too.
    clauses = [
        "v.audit_run_id = :run",
        "a.is_global IS TRUE",
    ]
    params: dict[str, Any] = {}
    if bucket:
        clauses.append("v.bucket = :bucket")
        params["bucket"] = bucket
    # No bucket filter means every bucket, VERIFIED included.
    if reason:
        clauses.append("v.cleanup_reason = :reason")
        params["reason"] = reason
    if tier:
        clauses.append("v.usage_tier = :tier")
        params["tier"] = tier
    if verdict:
        clauses.append("v.verdict_kind = :verdict")
        params["verdict"] = verdict
    if cluster_id is not None:
        clauses.append("v.purpose_cluster_id = :cluster_id")
        params["cluster_id"] = cluster_id
    if rule:
        # `mm_audit_finding` is what `per_rule_hits` used to be: the entities
        # that failed one quality check. EXISTS rather than a JOIN so a row is
        # returned once however many findings it carries.
        clauses.append(
            "EXISTS (SELECT 1 FROM mm_audit_finding f "
            "         WHERE f.audit_run_id = v.audit_run_id "
            "           AND f.assistant_id = v.assistant_id AND f.rule = :rule)"
        )
        params["rule"] = rule
    if mcp:
        clauses.append(
            f"EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(a.mcp_servers, '[]'::jsonb)) e "
            f"         WHERE {_MCP_ID} = :mcp)"
        )
        params["mcp"] = mcp
    if category:
        clauses.append("COALESCE(a.categories, '[]'::jsonb) @> CAST(:category AS jsonb)")
        params["category"] = json.dumps([category])
    if q:
        clauses.append("(a.name ILIKE :q OR a.description ILIKE :q)")
        params["q"] = f"%{q}%"
    return " AND ".join(clauses), params


def cleanup_total(session, run_id: str, **filters: Any) -> int:
    where, params = _cleanup_filters(**filters)
    return int(
        _scalar(
            session,
            f"""
            SELECT count(*) FROM mm_audit_verdict v
              LEFT JOIN assistants a ON a.id = v.assistant_id
             WHERE {where}
            """,
            run=run_id,
            **params,
        )
        or 0
    )


_CLEANUP_ORDER_BY: dict[CleanupOrderBy, str] = {
    CleanupOrderBy.score_desc: "v.match_score DESC NULLS LAST, v.assistant_id",
    CleanupOrderBy.users_desc: "a.unique_users_count DESC NULLS LAST, v.assistant_id",
    CleanupOrderBy.users_asc: "a.unique_users_count ASC NULLS LAST, v.assistant_id",
    CleanupOrderBy.likes_desc: "a.unique_likes_count DESC NULLS LAST, v.assistant_id",
    CleanupOrderBy.likes_asc: "a.unique_likes_count ASC NULLS LAST, v.assistant_id",
    CleanupOrderBy.name_asc: "a.name ASC NULLS LAST, v.assistant_id",
    CleanupOrderBy.name_desc: "a.name DESC NULLS LAST, v.assistant_id",
}


def cleanup_page(
    session,
    run_id: str,
    *,
    page: int,
    page_size: int,
    order_by: Optional[CleanupOrderBy] = None,
    **filters: Any,
) -> list[dict]:
    """One page of the Cleanup table — `WHERE` / `ORDER BY` / `LIMIT` in SQL.

    Ordered by `assistant_id` by default (stable pagination without an ordering
    contract). Pass `order_by` to sort by match score, user count, like count,
    or name; `v.assistant_id` is always appended as a tiebreaker.
    """
    where, params = _cleanup_filters(**filters)
    order_clause = _CLEANUP_ORDER_BY.get(order_by, "v.assistant_id") if order_by else "v.assistant_id"
    return [
        dict(row)
        for row in _rows(
            session,
            f"""
            SELECT v.assistant_id                AS id,
                   a.name                        AS name,
                   a.creator                     AS creator,
                   COALESCE(a.categories, '[]'::jsonb) AS categories,
                   a.unique_users_count          AS unique_users_count,
                   a.unique_likes_count          AS unique_likes_count,
                   v.usage_tier, v.bucket, v.bucket_reason, v.verdict_kind,
                   v.cleanup_reason, v.canonical_id, v.canonical_name,
                   v.canonical_users, v.match_score, v.detail,
                   v.critical_count, v.optional_count
              FROM mm_audit_verdict v
              LEFT JOIN assistants a ON a.id = v.assistant_id
             WHERE {where}
             ORDER BY {order_clause}
             LIMIT :limit OFFSET :offset
            """,
            run=run_id,
            limit=page_size,
            offset=max(0, (page - 1) * page_size),
            **params,
        )
    ]


def top_similarity_for(session, run_id: str, assistant_ids: list) -> dict:
    """The rank-1 similarity row per assistant, for one page of the table.

    Bounded by the page: `assistant_ids` is at most `page_size` long. Replaces
    `SimilarityResult.top_pair()`, which existed only in the process that ran
    the audit.
    """
    if not assistant_ids:
        return {}
    rows = _rows(
        session,
        """
        SELECT s.assistant_id,
               s.other_id,
               -- LEFT JOIN, not INNER: the matched assistant may since have
               -- been deleted, and losing the whole similarity row because its
               -- counterpart is gone would be worse than showing no name.
               a.name AS other_name,
               a.slug AS b_slug,
               a.project AS b_project,
               s.combined, s.severity, s.reason, s.signals,
               ov.verdict_kind AS b_verdict_kind
          FROM mm_audit_similarity s
          LEFT JOIN assistants a ON a.id = s.other_id
          LEFT JOIN mm_audit_verdict ov
            ON ov.audit_run_id = s.audit_run_id AND ov.assistant_id = s.other_id
         WHERE s.audit_run_id = :run AND s.rank = 1 AND s.assistant_id = ANY(:ids)
        """,
        run=run_id,
        ids=list(assistant_ids),
    )
    return {
        row["assistant_id"]: {
            "a_id": row["assistant_id"],
            "b_id": row["other_id"],
            "b_name": row["other_name"],
            "b_slug": row["b_slug"],
            "b_project": row["b_project"],
            "signals": row["signals"] or {},
            "combined": row["combined"],
            "severity": row["severity"],
            "reason": row["reason"],
            "b_verdict_kind": row["b_verdict_kind"],
        }
        for row in rows
    }


# ---------------------------------------------------------------------------
# Facets — design-persistence.md §5.3
# ---------------------------------------------------------------------------


def run_facets(session, run_id: str) -> dict:
    """Every run-scoped vocabulary in one round trip (the §5.3 `UNION ALL`).

    Returns `{facet: [(value, count), ...]}` ordered by count descending, for
    `reason`, `bucket`, `tier`, `verdict` (from `mm_audit_verdict`) and `rule`,
    `severity` (from `mm_audit_finding`).
    """
    rows = _rows(
        session,
        """
        SELECT 'reason'   AS facet, cleanup_reason AS value, count(*) AS n
          FROM mm_audit_verdict WHERE audit_run_id = :run AND cleanup_reason IS NOT NULL GROUP BY 2
        UNION ALL
        SELECT 'bucket',   bucket,       count(*)
          FROM mm_audit_verdict WHERE audit_run_id = :run AND bucket IS NOT NULL GROUP BY 2
        UNION ALL
        SELECT 'tier',     usage_tier,   count(*)
          FROM mm_audit_verdict WHERE audit_run_id = :run AND usage_tier IS NOT NULL GROUP BY 2
        UNION ALL
        SELECT 'verdict',  verdict_kind, count(*)
          FROM mm_audit_verdict WHERE audit_run_id = :run AND verdict_kind IS NOT NULL GROUP BY 2
        UNION ALL
        SELECT 'rule',     rule,         count(*)
          FROM mm_audit_finding WHERE audit_run_id = :run GROUP BY 2
        UNION ALL
        SELECT 'severity', severity,     count(*)
          FROM mm_audit_finding WHERE audit_run_id = :run GROUP BY 2
        """,
        run=run_id,
    )
    out: dict[str, list] = {}
    for row in rows:
        out.setdefault(row["facet"], []).append((row["value"], int(row["n"])))
    for values in out.values():
        values.sort(key=lambda kv: (-kv[1], kv[0] or ""))
    return out


def rule_severities(session, run_id: str) -> dict:
    """`rule -> severity` for the run. One row per distinct rule."""
    rows = _rows(
        session,
        """
        SELECT rule, max(severity) AS severity FROM mm_audit_finding
         WHERE audit_run_id = :run GROUP BY rule
        """,
        run=run_id,
    )
    return {row["rule"]: row["severity"] for row in rows}


def cluster_facets(session, run_id: str) -> list:
    """Duplicate clusters with two or more members, largest first.

    `canonical_name` is the name of the cluster's highest-adoption member — the
    same choice `_build_clusters_from_neighbors` makes when it picks a cluster
    canonical, reproduced here as an ordered `array_agg` so the member records
    never have to be loaded. Falling back to `max(canonical_name)` off the
    verdict rows would name only the clusters that happen to contain a
    canonical verdict and label every other one "cluster 3".
    """
    rows = _rows(
        session,
        """
        SELECT v.purpose_cluster_id AS cluster_id,
               count(*)             AS size,
               (array_agg(a.name ORDER BY COALESCE(a.unique_users_count, 0) DESC))[1]
                                    AS canonical_name
          FROM mm_audit_verdict v
          LEFT JOIN assistants a ON a.id = v.assistant_id
         WHERE v.audit_run_id = :run AND v.purpose_cluster_id IS NOT NULL
         GROUP BY 1 HAVING count(*) >= 2
         ORDER BY 2 DESC, 1
        """,
        run=run_id,
    )
    return [
        {
            "cluster_id": row["cluster_id"],
            "size": int(row["size"]),
            "canonical_name": row["canonical_name"] or f"cluster {row['cluster_id']}",
        }
        for row in rows
    ]


def catalogue_facet(session, field: str, *, limit: Optional[int] = None) -> list:
    """MCP-server or category counts over the catalogue, with adoption.

    `field` is `mcps` or `categories`. Counted over `assistants` rather than
    over the run so the dropdowns are populated before the first audit — which
    is how the in-memory path behaved, because it read the adapter's records
    and not the `AuditResult`.
    """
    if field == "mcps":
        expand = (
            f"SELECT {_MCP_ID} AS value, COALESCE(a.unique_users_count, 0) AS users "
            f"  FROM assistants a, jsonb_array_elements(COALESCE(a.mcp_servers, '[]'::jsonb)) e "
            f" WHERE {_CATALOGUE_SCOPE}"
        )
    elif field == "categories":
        expand = (
            "SELECT e #>> '{}' AS value, COALESCE(a.unique_users_count, 0) AS users "
            "  FROM assistants a, jsonb_array_elements(COALESCE(a.categories, '[]'::jsonb)) e "
            f" WHERE {_CATALOGUE_SCOPE}"
        )
    else:  # pragma: no cover - programming error
        raise ValueError(f"unknown catalogue facet {field!r}")

    sql = (
        f"WITH expanded AS ({expand}) "
        "SELECT value, count(*) AS n, sum(users) AS total_users "
        "  FROM expanded WHERE value IS NOT NULL "
        " GROUP BY value ORDER BY n DESC, value"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [
        {"value": row["value"], "count": int(row["n"]), "total_users": int(row["total_users"] or 0)}
        for row in _rows(session, sql)
    ]


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def top_clusters(session, run_id: str, *, limit: int = 5) -> list:
    """The largest duplicate clusters, with the member the audit would keep.

    `label` is the two most frequent words longer than three characters across
    the members' names — the same derivation the in-memory path performed, moved
    behind a `string_agg` so the member records never have to be loaded.
    """
    rows = _rows(
        session,
        f"""
        SELECT v.purpose_cluster_id AS cluster_id,
               count(*)             AS size,
               sum(COALESCE(a.unique_users_count, 0)) AS total_users,
               (array_agg(a.id   ORDER BY COALESCE(a.unique_users_count, 0) DESC))[1] AS canonical_id,
               (array_agg(a.name ORDER BY COALESCE(a.unique_users_count, 0) DESC))[1] AS canonical_name,
               max(COALESCE(a.unique_users_count, 0)) AS canonical_users,
               string_agg(COALESCE(a.name, ''), ' ')  AS member_names
          FROM mm_audit_verdict v
          JOIN assistants a ON a.id = v.assistant_id
         WHERE v.audit_run_id = :run AND v.purpose_cluster_id IS NOT NULL
         GROUP BY 1 HAVING count(*) >= 2
         ORDER BY 2 DESC, 1
         LIMIT {int(limit)}
        """,
        run=run_id,
    )
    out = []
    for row in rows:
        words: Counter = Counter(token for token in (row["member_names"] or "").lower().split() if len(token) > 3)
        out.append(
            {
                "cluster_id": row["cluster_id"],
                "size": int(row["size"]),
                "label": " ".join(w for w, _ in words.most_common(2)) or "cluster",
                "canonical_id": row["canonical_id"],
                "canonical_name": row["canonical_name"],
                "canonical_users": row["canonical_users"],
                "total_users": int(row["total_users"] or 0),
            }
        )
    return out


def bucket_distribution(session, run_id: str) -> dict:
    counts = dict(_BUCKET_SKELETON)
    for row in _rows(
        session,
        "SELECT bucket, count(*) AS n FROM mm_audit_verdict "
        " WHERE audit_run_id = :run AND bucket IS NOT NULL GROUP BY 1",
        run=run_id,
    ):
        counts[row["bucket"]] = int(row["n"])
    return counts
