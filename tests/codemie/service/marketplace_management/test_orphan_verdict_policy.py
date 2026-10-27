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

"""Verdict rows for a deleted assistant (EXECUTION-PLAN 13.2 item 1; F-39, R12, S16).

THE POLICY, RESTATED AND PINNED HERE
------------------------------------
`design-persistence.md` §2.4 deliberately gives `mm_audit_verdict.assistant_id`
no foreign key to `assistants.id`, because moderation history must be able to
outlive the entity it describes — a reviewer must not be able to erase the
record of what they decided by deleting the thing they decided about. So what
happens to those rows on delete cannot be a cascade; it has to be a stated
policy. It is, and this module is where the decision is asserted rather than
merely written down:

    PURGE the verdicts. ANONYMISE the decision.

A verdict is a *machine's opinion of content that no longer exists*. Its
`detail` quotes the assistant's own name and prompt, it names no person, and it
is the row that resurrects a deleted assistant as a Cleanup entry with a NULL
name and a 404 behind it (F-39: `audit_read_repository.cleanup_page` LEFT JOINs
`assistants`, so a missing assistant does not remove the row). Keeping it
anonymised would be strictly worse than deleting it: an anonymous verdict is
un-actionable (nothing can be cleaned up), un-auditable (it accuses nobody of
anything) and still a retention liability, because the quoted prompt *is* the
deleted content. The accountability record §2.4 exists to protect is
`mm_review_decision` — which administrator decided what, from which status, at
which version, when — and that row survives, with only the free text that
quoted the deleted content replaced by a tombstone.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE HOOK SHIPPED IN 8.7
------------------------------------------------------------
Phase 11 measured 1 orphan verdict row on the dev database; Phase 12 measured 3
— *after* the purge-on-delete hook shipped. The hook is not broken and the
sweep is not misconfigured. The gap is a race, and the evidence for it is on
the row itself: for `9700c7a2-…`, `mm_review_decision.redacted_at` is set (so
the hook did run for that assistant) while its verdict row, written by an audit
run that started 162 ms *after* the decision, survives.

    audit run reads the corpus ──────────────────────────────────┐
                    delete → hook purges 0 verdicts (none yet)    │
                                     audit run writes verdicts ───┘  ← orphan

A hook that runs at delete time cannot delete a row that does not exist yet, and
the daily sweep is up to 24 hours away. So the run that *creates* the orphan is
the only place that can also be sure to reap it, and `_persist` now applies the
same policy — the same repository, the same statements — as its last act.

Both halves are asserted below, against a real migrated PostgreSQL: the hook's
half (delete after the run) and the race's half (delete during the run).
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from codemie.service.marketplace_management.models import EntityKind

_SCHEMA = "codemie"

# One assistant, shaped so the deterministic gates have something to say about
# it (a one-character name and description trip several CRITICAL rules), so the
# run genuinely writes a verdict row rather than skipping a clean record.
GHOST_ID = "orphan-policy-ghost"
KEEPER_ID = "orphan-policy-keeper"

_SEED = (
    (GHOST_ID, "x", "x", "x", "[]"),
    (
        KEEPER_ID,
        "Release Notes Summariser",
        "Reads a merged pull request and writes the customer-facing release note "
        "for it, in the tone the documentation team already uses.",
        "You are a release-notes writer. Read the diff, identify user-visible "
        "changes, and write one short paragraph per change. Never invent a change "
        "that is not in the diff, and always name the affected component.",
        '["Development"]',
    ),
)


@pytest.fixture
def db(mm_pg_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """The shared throwaway database, seeded and then emptied of what we added.

    `audit_service` and the purge repository both build their sessions from
    `PostgresClient.get_engine()`, so overriding the engine puts the production
    code path on this database unmodified.
    """
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: mm_pg_engine))

    with mm_pg_engine.connect() as conn:
        # `mm_review_decision.decided_by_id` FKs `users.id`: a decision cannot
        # name a principal that does not exist, which is itself part of why the
        # accountability record is worth keeping when the assistant goes.
        conn.execute(
            text(
                f"insert into {_SCHEMA}.users (id, username, email, name, is_admin, is_active, date) "
                "values ('seed-creator', 'seed-creator', 'reviewer@codemie.ai', "
                "        'Seed Reviewer', true, true, now()) on conflict (id) do nothing"
            )
        )
        for aid, name, description, prompt, categories in _SEED:
            conn.execute(
                text(
                    f"insert into {_SCHEMA}.assistants "
                    "(id, name, description, system_prompt, project, shared, is_react, "
                    " created_date, creator, is_global, categories, marketplace_status, "
                    " unique_users_count, unique_likes_count, unique_dislikes_count, version_count) "
                    "values (:id, :n, :d, :p, 'demo', true, true, now(), 'seed-creator', true, "
                    "        cast(:c as jsonb), 'APPROVED', 3, 1, 0, 1)"
                ),
                {"id": aid, "n": name, "d": description, "p": prompt, "c": categories},
            )
        conn.commit()

    yield mm_pg_engine

    with mm_pg_engine.connect() as conn:
        for statement in (
            f"delete from {_SCHEMA}.mm_audit_run",
            f"delete from {_SCHEMA}.mm_audit_finding",
            f"delete from {_SCHEMA}.mm_audit_similarity",
            f"delete from {_SCHEMA}.mm_audit_verdict",
            f"delete from {_SCHEMA}.mm_llm_assessment",
            f"delete from {_SCHEMA}.mm_review_decision",
            f"delete from {_SCHEMA}.assistants",
            f"delete from {_SCHEMA}.users where id = 'seed-creator'",
        ):
            conn.execute(text(statement))
        conn.commit()


def _verdicts_for(engine: Engine, assistant_id: str) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(f"select count(*) from {_SCHEMA}.mm_audit_verdict where assistant_id = :a"),
                {"a": assistant_id},
            ).scalar()
            or 0
        )


def _orphan_verdicts(engine: Engine) -> list[str]:
    """Exactly the query the live audit used to measure the 1 and then the 3."""
    with engine.connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                text(
                    f"""
                    select distinct v.assistant_id
                      from {_SCHEMA}.mm_audit_verdict v
                      join {_SCHEMA}.mm_audit_run r on r.id = v.audit_run_id
                     where r.entity_kind = 'ASSISTANT'
                       and not exists (
                           select 1 from {_SCHEMA}.assistants a where a.id = v.assistant_id
                       )
                     order by 1
                    """
                )
            ).all()
        ]


def _delete_assistant_row(engine: Engine, assistant_id: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f"delete from {_SCHEMA}.assistants where id = :a"), {"a": assistant_id})
        conn.commit()


def _run_audit() -> None:
    from codemie.service.marketplace_management.audit_service import AuditCache

    AuditCache().run_audit(EntityKind.ASSISTANT, include_es_similarity=False)


# ---------------------------------------------------------------------------
# The hook's half: delete AFTER the run
# ---------------------------------------------------------------------------


def test_deleting_an_audited_assistant_purges_its_verdicts(db: Engine) -> None:
    """Create, audit, delete — the verdict rows go, the decision would stay.

    This is the policy as stated: PURGE. It passes today; it is here because a
    policy nobody asserts is a comment, and because the race test below would
    otherwise be indistinguishable from "the hook never worked".
    """
    from codemie.rest_api.models.assistant import Assistant

    _run_audit()
    assert _verdicts_for(db, GHOST_ID) == 1, "the audit wrote no verdict to purge"

    _delete_assistant_row(db, GHOST_ID)
    Assistant._purge_marketplace_moderation_data(GHOST_ID)

    assert _verdicts_for(db, GHOST_ID) == 0, (
        "the verdict rows of a deleted assistant survived the purge hook; they are a "
        "machine's opinion of content that no longer exists and F-39 renders them as "
        "a Cleanup row with a NULL name"
    )
    assert (
        _verdicts_for(db, KEEPER_ID) == 1
    ), "the purge took a surviving assistant's verdict with it — it is scoped to one id"
    assert _orphan_verdicts(db) == []


# ---------------------------------------------------------------------------
# The race's half: delete DURING the run
# ---------------------------------------------------------------------------


def test_a_run_that_outlives_its_subject_leaves_no_orphan_verdict(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """The measured defect: 1 orphan in Phase 11, 3 in Phase 12, hook armed.

    An audit reads the corpus, spends seconds (or, before the ES fix, minutes)
    computing, then writes. An assistant deleted inside that window is purged of
    moderation data it does not have yet, and acquires a verdict row afterwards
    that nothing will reap until the next daily sweep.

    Reproduced exactly, and deterministically: the corpus is snapshotted, the
    assistant is deleted through the production hook, and the run then persists
    the snapshot it was already holding. Everything from `run_audit` onwards is
    the real code path.
    """
    from codemie.rest_api.models.assistant import Assistant
    from codemie.service.marketplace_management import entity_adapter

    # 1. What the in-flight run is already holding.
    snapshot = entity_adapter.get_adapter(EntityKind.ASSISTANT).list_records()
    assert any(r.id == GHOST_ID for r in snapshot), "the corpus snapshot is missing the subject"

    # 2. The delete lands, with the real hook, while that run is still working.
    _delete_assistant_row(db, GHOST_ID)
    Assistant._purge_marketplace_moderation_data(GHOST_ID)

    # 3. The run finishes and writes the corpus it read in step 1.
    monkeypatch.setattr(entity_adapter.AssistantAdapter, "list_records", lambda self: snapshot)
    _run_audit()

    orphans = _orphan_verdicts(db)
    assert orphans == [], (
        f"the completed run left verdict rows for {orphans}, which no longer exist in "
        "`assistants`. The purge hook cannot reap a row written after it ran, and the "
        "sweep is up to a day away — so the run that creates the orphan must apply the "
        "same policy before it closes."
    )
    assert _verdicts_for(db, KEEPER_ID) == 1, "the post-run purge took a surviving assistant's verdict with it"


def test_the_post_run_purge_keeps_the_decision_and_drops_the_prompt(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PURGE and ANONYMISE are one policy, and the run applies both halves.

    §2.4's missing foreign key exists so that `mm_review_decision` can outlive
    the assistant. A post-run purge that deleted it would defeat the exact
    guarantee the missing FK buys, so the row is asserted to survive — stamped
    `redacted_at`, with the free text that quoted the deleted assistant gone.
    """
    from codemie.service.marketplace_management import entity_adapter

    with db.connect() as conn:
        conn.execute(
            text(
                f"insert into {_SCHEMA}.mm_review_decision "
                "(id, entity_kind, entity_id, decision, from_status, to_status, "
                " from_version, to_version, decided_by_id, decided_by_email, "
                " idempotency_key, reason, created_at) "
                "values (gen_random_uuid()::text, 'ASSISTANT', :a, 'APPROVE', "
                "        'PENDING_REVIEW', 'APPROVED', 0, 1, 'seed-creator', "
                "        'reviewer@codemie.ai', :key, :reason, now())"
            ),
            {
                "a": GHOST_ID,
                "key": f"orphan-policy-{GHOST_ID}-approve",
                "reason": "Approved: the prompt reads well and the scope is clear.",
            },
        )
        conn.commit()

    snapshot = entity_adapter.get_adapter(EntityKind.ASSISTANT).list_records()
    _delete_assistant_row(db, GHOST_ID)
    monkeypatch.setattr(entity_adapter.AssistantAdapter, "list_records", lambda self: snapshot)
    _run_audit()

    with db.connect() as conn:
        row = (
            conn.execute(
                text(
                    f"select decision, from_status, to_status, decided_by_id, reason, "
                    f"       redacted_at is not null as redacted "
                    f"  from {_SCHEMA}.mm_review_decision where entity_id = :a"
                ),
                {"a": GHOST_ID},
            )
            .mappings()
            .first()
        )

    assert row is not None, (
        "the post-run purge deleted the review decision. That row is the accountability "
        "record §2.4's missing foreign key exists to preserve — who decided what, when — "
        "and deleting it lets a reviewer erase the evidence of their own decision by "
        "deleting the assistant afterwards"
    )
    assert (row["decision"], row["from_status"], row["to_status"]) == (
        "APPROVE",
        "PENDING_REVIEW",
        "APPROVED",
    )
    assert row["decided_by_id"] == "seed-creator", "the actor must survive; that is the whole point"
    assert row["redacted"], "a redaction that is not recorded cannot be audited"
    assert "prompt reads well" not in (row["reason"] or ""), "the free text still quotes the deleted assistant"
