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

"""Purpose evidence still builds clusters; the purpose-derived rules stay off.

THE QUESTION THIS MODULE ANSWERS
--------------------------------
`semantic_clone` and `big_cluster_surplus` were removed from the audit, and
`purpose_duplicate` is switched off pending recalibration (cosine >= 0.70 flagged
~85% of COLD/DEAD assistants). This module used to prove those reasons were
producible; it now pins the opposite, and the part that must keep working:

1. **The cluster display still works.** The ES augmentation fills
   `purpose_neighbors` and `_build_clusters_from_neighbors` derives the cluster
   maps from them. They feed the Overview's Top clusters table and the Cleanup
   page's cluster filter, so a corpus with a purpose chain must still produce one
   cluster.
2. **No verdict is derived from them.** A low-usage clone in that cluster must not
   come back as `purpose_duplicate` or `semantic_clone`, in memory or in
   `mm_audit_verdict`.
3. **The neighbour's usage is real.** The augmentation once recorded
   `"other_users": 0`; the parked purpose rule compares usage counts, so the
   figure must stay correct for when it returns.

WHAT IS ASSERTED, AND WHY THE STUB IS THE HONEST CHOICE
--------------------------------------------------------
The seam under test is `run_audit`'s, not Elasticsearch's, so the KNN answer is
stubbed and everything on this side of it is real: the real engine, the real
union-find cluster builder, the real aggregation, the real `_persist`, and a
real migrated PostgreSQL underneath (`mm_pg_engine`). A test that reached the
live index would assert on whatever that index happens to hold today — which is
exactly the ambiguity this module exists to remove, because it would make "no rows" depend on
what the index holds today.

THE CORPUS
----------
The stub returns a chain rather than a clique, and clustering is transitive
(union-find over mutual edges), so all four records land in one cluster. It is
the shape that used to reach each removed rule:

    canonical(200u) <-> clone-1(1u) <-> clone-2(1u) <-> clone-3(1u)

    clone-1  sees the canonical directly            -> used to be purpose_duplicate
    clone-2  sees only 1-user neighbours, but its
             cluster's canonical has 200 users      -> used to be semantic_clone
    clone-3  likewise                               -> used to be semantic_clone

Twelve well-used filler assistants sit alongside them, because usage tiers are
percentile-based over the corpus: with only four records the 50th percentile of
`[1, 1, 1, 200]` is 1, every clone comes out WARM, and the grandfathering rule
(EPMCDME-14231 — actively used assistants surface as REVIEW, never as CLEANUP)
would suppress both reasons for a reason that has nothing to do with this bug.
The filler moves the clones into the bottom decile, which is where a real
low-usage clone sits.

Each record's own text is deliberately unlike the others'. If they shared
wording, the *structural* duplicate rule (rule 1, which needs no purpose data at
all) would fire first and the test would pass while proving nothing.
"""

from __future__ import annotations

from typing import Iterator, Optional

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from codemie.service.marketplace_management.models import EntityKind

_SCHEMA = "codemie"

CANONICAL_ID = "purpose-canonical"
CLONE_1, CLONE_2, CLONE_3 = "purpose-clone-1", "purpose-clone-2", "purpose-clone-3"
CLUSTER_IDS = (CANONICAL_ID, CLONE_1, CLONE_2, CLONE_3)

# `(id, name, users, description, system_prompt)`.
_CLUSTER_SEED = (
    (
        CANONICAL_ID,
        "Release Notes Summariser",
        200,
        "Reads a merged pull request and writes the customer-facing release note for it, "
        "in the tone the documentation team already uses.",
        "You are a release-notes writer. Read the diff, identify user-visible changes, and "
        "write one short paragraph per change. Never invent a change that is not in the "
        "diff, and always name the affected component.",
    ),
    (
        CLONE_1,
        "Changelog Composer",
        1,
        "Turns a list of merged commits into a dated changelog entry that follows the "
        "Keep a Changelog headings our repositories already use.",
        "Group the commits you are given under Added, Changed, Fixed and Removed. Drop "
        "anything that is purely internal. Write one line per entry, present tense, and "
        "never merge two unrelated commits into a single line.",
    ),
    (
        CLONE_2,
        "Ship Note Author",
        1,
        "Drafts the short paragraph that goes out to customers whenever a version ships, "
        "built from the tickets that were closed in it.",
        "Given a set of closed tickets, decide which ones a customer would notice. Write a "
        "single paragraph in plain language for those, and list the rest as maintenance. "
        "Ask for the version number if it was not supplied.",
    ),
    (
        CLONE_3,
        "Version Digest Builder",
        1,
        "Assembles the digest that accompanies a tagged version: what shipped, what broke "
        "and what an operator has to do before upgrading.",
        "Work from the tag range you are given. Produce three sections: highlights, known "
        "issues and upgrade steps. Leave a section out entirely rather than filling it "
        "with filler, and never guess an upgrade step.",
    ),
)

# Twelve unrelated, well-used assistants. Their only job is to make the corpus
# big enough and busy enough that one user is genuinely the bottom decile.
_FILLER_TEXTS = (
    (
        "Incident Timeline Builder",
        180,
        "Reconstructs the minute-by-minute timeline of a production incident from alert "
        "payloads, chat transcripts and deploy records.",
        "Order every event you are given by timestamp, in UTC. Mark the first alert, the "
        "first human acknowledgement and the mitigation. Do not speculate about cause; "
        "that belongs in the review, not the timeline.",
    ),
    (
        "SQL Query Explainer",
        165,
        "Explains what a SQL statement does, which indexes it can use and where it will "
        "get slow, in language a reviewer can act on.",
        "Read the statement and describe its result set first. Then name the joins, the "
        "filters and the ordering, and say which of them an index would help. Never "
        "rewrite the query unless you are asked to.",
    ),
    (
        "Terraform Plan Reviewer",
        150,
        "Reads a terraform plan and reports which changes are destructive, which are "
        "in-place and which are pure metadata.",
        "Classify every resource change as create, update-in-place, replace or destroy. "
        "Call out replacements of stateful resources explicitly. Quote the attribute that "
        "forces each replacement.",
    ),
    (
        "Meeting Notes Condenser",
        140,
        "Turns a raw meeting transcript into decisions, owners and open questions, and " "nothing else.",
        "Produce three lists: decisions taken, actions with an owner, and questions left "
        "open. Attribute nothing to a person who did not say it, and leave a list empty "
        "rather than inventing an entry.",
    ),
    (
        "API Contract Differ",
        130,
        "Compares two OpenAPI documents and reports which changes are breaking for an " "existing client.",
        "Walk both documents path by path. Report removed operations, narrowed types and "
        "newly required fields as breaking; report additions as compatible. Cite the JSON "
        "pointer for every finding.",
    ),
    (
        "Dockerfile Slimmer",
        120,
        "Suggests concrete size and cache reductions for a Dockerfile without changing what " "the image can do.",
        "Identify layers that can be merged, files that should not be copied, and packages "
        "that belong in a build stage. Give the replacement lines. Never propose a change "
        "that alters the runtime behaviour.",
    ),
    (
        "Regex Explainer",
        110,
        "Explains a regular expression in plain language and points out the cases it will " "match by accident.",
        "Break the pattern into its groups and describe each. Then give three strings it "
        "matches and three near-misses it does not. Warn about catastrophic backtracking "
        "where the pattern permits it.",
    ),
    (
        "Log Pattern Finder",
        95,
        "Reads a log excerpt and groups the lines into the handful of distinct events " "actually present in it.",
        "Collapse the lines into patterns by replacing the parts that vary. Report each "
        "pattern with its count and its first and last occurrence. Do not discard a pattern "
        "because it only appears once.",
    ),
    (
        "Test Name Critic",
        80,
        "Reviews test names and says what each one actually asserts, so a misleading name "
        "is visible before it misleads somebody.",
        "For each test, state the behaviour its body verifies. Where the name and the body "
        "disagree, say so and propose a name that matches the body. Never propose changing "
        "the assertion.",
    ),
    (
        "Cron Schedule Translator",
        70,
        "Translates cron expressions into sentences and back, including the timezone and " "daylight-saving traps.",
        "Describe when the expression fires, in the timezone you are given. Name the days "
        "it will skip or repeat across a daylight-saving boundary. Refuse to guess a "
        "timezone that was not supplied.",
    ),
    (
        "Error Message Rewriter",
        60,
        "Rewrites an error message so that it says what happened, what the effect is and "
        "what the reader can do next.",
        "Keep every fact from the original message. Add nothing that is not implied by it. "
        "Produce one sentence of cause, one of effect and one of remedy, and drop any stack "
        "frame that the reader cannot act on.",
    ),
    (
        "Feature Flag Auditor",
        45,
        "Lists the feature flags a codebase reads and reports which of them no longer have "
        "a false branch worth keeping.",
        "For every flag, report where it is read and what each branch does. Mark a flag as "
        "retirable only when one branch is unreachable. Quote the line that makes it "
        "unreachable.",
    ),
)

_FILLER_SEED = tuple(
    (f"purpose-filler-{n}", name, users, description, prompt)
    for n, (name, users, description, prompt) in enumerate(_FILLER_TEXTS)
)

_ALL_SEED = _CLUSTER_SEED + _FILLER_SEED

# The chain the stubbed KNN backend reports. Mutual by construction, because
# `_build_clusters_from_neighbors` deliberately refuses to union on a one-way
# edge (a plain "any edge above threshold" rule collapses the whole corpus into
# one transitive blob).
_PURPOSE_EDGES = ((CANONICAL_ID, CLONE_1), (CLONE_1, CLONE_2), (CLONE_2, CLONE_3))

# Comfortably above the CRITICAL similarity threshold (0.85 by default), which
# is the bar both the cluster builder and rule 2 apply.
_PURPOSE_COSINE = 0.95


@pytest.fixture
def db(mm_pg_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: mm_pg_engine))

    with mm_pg_engine.connect() as conn:
        for aid, name, users, description, prompt in _ALL_SEED:
            conn.execute(
                text(
                    f"insert into {_SCHEMA}.assistants "
                    "(id, name, description, system_prompt, project, shared, is_react, "
                    " created_date, update_date, creator, is_global, categories, "
                    " marketplace_status, unique_users_count, unique_likes_count, "
                    " unique_dislikes_count, version_count) "
                    "values (:id, :n, :d, :p, 'demo', true, true, now(), now(), "
                    "        'seed-creator', true, cast('[\"Development\"]' as jsonb), "
                    "        'APPROVED', :u, 1, 0, 1)"
                ),
                {"id": aid, "n": name, "d": description, "p": prompt, "u": users},
            )
        conn.commit()

    yield mm_pg_engine

    with mm_pg_engine.connect() as conn:
        for statement in (
            f"delete from {_SCHEMA}.mm_audit_run",
            f"delete from {_SCHEMA}.mm_audit_finding",
            f"delete from {_SCHEMA}.mm_audit_similarity",
            f"delete from {_SCHEMA}.mm_audit_verdict",
            f"delete from {_SCHEMA}.assistants",
        ):
            conn.execute(text(statement))
        conn.commit()


@pytest.fixture
def es_reports_a_purpose_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """A KNN backend that answers `_PURPOSE_EDGES`, and `[]` for everyone else."""
    from codemie.service.marketplace_management import es_similarity

    names = {aid: name for aid, name, _u, _d, _p in _ALL_SEED}
    adjacency: dict[str, set[str]] = {aid: set() for aid in names}
    for left, right in _PURPOSE_EDGES:
        adjacency[left].add(right)
        adjacency[right].add(left)

    def _knn_batch(items, k=10, chunk_size=None):  # noqa: ANN001 - mirrors the real signature
        return {
            aid: [
                es_similarity.ESNeighbor(assistant_id=other, name=names[other], score=_PURPOSE_COSINE)
                for other in sorted(adjacency.get(aid, ()))
            ]
            for aid, _query in items
        }

    index = es_similarity.MarketplaceAssistantsIndex
    monkeypatch.setattr(index, "knn_batch", classmethod(lambda cls, *a, **kw: _knn_batch(*a, **kw)))
    monkeypatch.setattr(index, "doc_count", classmethod(lambda cls: len(_ALL_SEED)))
    monkeypatch.setattr(index, "index_name", classmethod(lambda cls: "marketplace_assistants"))
    monkeypatch.setattr(index, "reset_similarity_circuit", classmethod(lambda cls: None))
    monkeypatch.setattr(index, "similarity_health", classmethod(lambda cls: (False, None)))


def _run(*, include_es_similarity: bool = True):
    from codemie.service.marketplace_management.audit_service import AuditCache

    return AuditCache().run_audit(EntityKind.ASSISTANT, include_es_similarity=include_es_similarity)


def _reasons(result) -> dict[str, Optional[str]]:
    return {aid: (verdict.reason.value if verdict.reason else None) for aid, verdict in result.verdicts.items()}


_SWITCHED_OFF_REASONS = ("purpose_duplicate", "semantic_clone")


def test_a_purpose_cluster_is_built_but_derives_no_cleanup_verdict(
    db: Engine, es_reports_a_purpose_chain: None
) -> None:
    """Three low-usage assistants chained to one 200-user canonical at cosine 0.95.

    The cluster must exist (display feature) and none of its members may be
    recommended for cleanup as a copy of something.
    """
    result = _run()

    assert result.purpose_cluster_id, (
        "the ES augmentation built no clusters at all — the stub, not the aggregation, "
        "is what failed, and nothing below would mean anything"
    )
    cluster_of = result.purpose_cluster_id
    assert len({cluster_of.get(aid) for aid in CLUSTER_IDS}) == 1, (
        "the four seeded records did not land in one cluster: " f"{ {aid: cluster_of.get(aid) for aid in CLUSTER_IDS} }"
    )

    reasons = _reasons(result)
    for aid in CLUSTER_IDS:
        assert reasons[aid] not in _SWITCHED_OFF_REASONS, (
            f"{aid} came back as {reasons[aid]!r}; the purpose-derived duplicate rules are switched off "
            "and the cluster rules are removed"
        )


def test_no_switched_off_reason_reaches_the_database(db: Engine, es_reports_a_purpose_chain: None) -> None:
    """`/cleanup?reason=...` reads `mm_audit_verdict.cleanup_reason`.

    The column is asserted and not the in-memory result, because that is what the
    Cleanup page lists. `bucket_reason` is checked beside it, including the removed
    `big_cluster_surplus`.
    """
    _run()

    with db.connect() as conn:
        rows = conn.execute(
            text(f"select assistant_id, cleanup_reason, bucket_reason from {_SCHEMA}.mm_audit_verdict")
        ).all()

    assert rows, "the audit persisted no verdicts"
    for assistant_id, cleanup_reason, bucket_reason in rows:
        assert cleanup_reason not in _SWITCHED_OFF_REASONS, (assistant_id, cleanup_reason)
        assert bucket_reason not in (*_SWITCHED_OFF_REASONS, "big_cluster_surplus"), (assistant_id, bucket_reason)


def test_the_canonical_neighbour_carries_its_real_usage_count(db: Engine, es_reports_a_purpose_chain: None) -> None:
    """`purpose_duplicate` compares usage counts, so they have to be real ones.

    The augmentation used to write `"other_users": 0` into every neighbour it
    recorded, which is a second, independent reason rule 2 could never fire:
    `pn["other_users"] >= 3 * max(1, mine)` is `0 >= 3` for every record in the
    marketplace. Asserted separately from the verdict so a regression here reads
    as "the neighbour lost its usage count" and not as "the rule stopped firing".
    """
    result = _run()

    neighbours = result.purpose_neighbors.get(CLONE_1) or []
    canonical = next((n for n in neighbours if n["other_id"] == CANONICAL_ID), None)

    assert canonical is not None, "the canonical is missing from its clone's neighbours"
    assert canonical["other_users"] == 200, (
        f"the canonical neighbour reports {canonical['other_users']} users; it has 200. "
        "A hardcoded 0 makes every usage comparison in `aggregate_verdict` false"
    )


def test_a_run_without_similarity_still_produces_the_verdicts_it_always_did(
    db: Engine, es_reports_a_purpose_chain: None
) -> None:
    """The re-derivation must be a no-op when there is no purpose evidence.

    `include_es_similarity=False` — and, in production, any run whose similarity
    backend came back UNAVAILABLE — leaves the purpose maps empty. Re-deriving
    verdicts from empty evidence has to return exactly what the single pass
    returned before, or the fix for the unreachable reasons would silently have
    changed the meaning of every degraded run.
    """
    result = _run(include_es_similarity=False)

    assert not result.purpose_cluster_id, "similarity was off; there should be no clusters"
    reasons = _reasons(result)
    assert (
        reasons[CLONE_1] is None and reasons[CLONE_2] is None
    ), f"a run with no purpose evidence produced purpose-derived reasons: {reasons}"
