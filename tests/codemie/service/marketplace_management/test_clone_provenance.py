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

"""The clone edge has to survive the whole path, not just the scorer.

EXECUTION-PLAN 9.1 / F-70. `assistant_clone_event` has recorded clone activity
since before this feature existed, and the duplicate detector never read it.
Adding a `clone` signal to `score_pair` only matters if a real clone, recorded
by the real create endpoint, reaches it — so this module exercises the whole
chain against a migrated PostgreSQL:

    router writes the edge -> repository reads it -> adapter hands it to the
    engine -> engine scores the pair with it.

Every link is a place the signal could quietly become inert, which is the
failure mode the whole phase is about (F-13: wired, weighted, displayed, and
arithmetically incapable of firing).

What the edge does *not* do is force a duplicate verdict forever. It gates on
the pair's current structural score, so a clone whose owner has substantially
reworked it is judged on its content rather than on provenance (MR !4070
review comment #23) — the seeded pair here is deliberately that case.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session

from codemie.service.marketplace_management.models import EntityKind
from codemie.service.marketplace_management.validator.models import Severity

_SCHEMA = "codemie"

# The source is popular; the clone has one user. `aggregate_verdict` only
# recommends cleanup for COLD/DEAD entries with a higher-usage canonical, so
# the asymmetry is what makes the duplicate verdict reachable at all.
_SOURCE_ID = "clone-source-1"
_CLONE_ID = "clone-child-1"

_DESCRIPTION = (
    "Reads a merged pull request and writes the customer-facing release note "
    "for it, in the tone the documentation team already uses."
)
_PROMPT = (
    "You are a release-notes writer. Read the diff, identify user-visible "
    "changes, and write one short paragraph per change. Never invent a change "
    "that is not in the diff, and always name the affected component."
)

# Deliberately disjoint structural signals. The clone is renamed and rebuilt
# with different tools -- exactly what a person does after cloning -- so no
# weighted signal links the two. Only the recorded edge does.
_SEED = (
    (_SOURCE_ID, "Release Notes Summariser", ["Development"], 120),
    (_CLONE_ID, "Budget Variance Explainer", ["Finance"], 1),
)


@pytest.fixture(scope="module")
def engine(mm_pg_engine: Engine) -> Engine:
    return mm_pg_engine


@pytest.fixture
def db(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """Point every production entry point at the throwaway database."""
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: engine))

    with engine.connect() as conn:
        for aid, name, categories, users in _SEED:
            conn.execute(
                text(
                    "insert into codemie.assistants "
                    "(id, name, description, system_prompt, project, shared, is_react, "
                    " created_date, creator, is_global, categories, marketplace_status, "
                    " unique_users_count, unique_likes_count, unique_dislikes_count, "
                    " version_count, clone_count) "
                    "values (:id, :n, :d, :p, 'demo', true, true, now(), 'seed-creator', true, "
                    "        cast(:c as jsonb), 'APPROVED', :u, 1, 0, 1, 0)"
                ),
                {
                    "id": aid,
                    "n": name,
                    "d": _DESCRIPTION,
                    "p": _PROMPT,
                    "c": '["%s"]' % categories[0],
                    "u": users,
                },
            )
        conn.commit()

    with Session(engine) as session:
        yield session
        session.rollback()

    with engine.connect() as conn:
        conn.execute(text(f"delete from {_SCHEMA}.mm_audit_run"))
        conn.execute(text(f"delete from {_SCHEMA}.assistant_clone_event"))
        conn.execute(text(f"delete from {_SCHEMA}.assistants"))
        conn.commit()


def _record_clone(source_id: str = _SOURCE_ID, clone_id: str = _CLONE_ID) -> None:
    """Record the edge exactly as `POST /v1/assistants` does on a clone."""
    from codemie.repository.assistants.assistant_clone_event_repository import (
        AssistantCloneEventRepository,
    )

    AssistantCloneEventRepository().log_clone_event(source_id, "cloning-user-1", clone_assistant_id=clone_id)


def test_the_edge_round_trips_through_the_repository(db: Session) -> None:
    """`log_clone_event` persists the child id and `list_clone_edges` reads it."""
    from codemie.repository.assistants.assistant_clone_event_repository import (
        AssistantCloneEventRepository,
    )

    _record_clone()

    edges = AssistantCloneEventRepository().list_clone_edges()

    assert (_SOURCE_ID, _CLONE_ID) in edges


def test_historic_rows_without_a_child_produce_no_edge(db: Session, engine: Engine) -> None:
    """Pre-migration rows are excluded rather than guessed at.

    The 11 rows already on the dev database were written before the column
    existed. There is no join, timestamp heuristic or naming convention that
    recovers which assistant each became, and a guessed duplicate edge marks a
    real assistant CRITICAL on invented evidence.
    """
    from codemie.repository.assistants.assistant_clone_event_repository import (
        AssistantCloneEventRepository,
    )

    with engine.connect() as conn:
        conn.execute(
            text(
                f"insert into {_SCHEMA}.assistant_clone_event "
                "(id, assistant_id, user_id, created_at) "
                "values ('legacy-1', :src, 'cloning-user-1', now())"
            ),
            {"src": _SOURCE_ID},
        )
        conn.commit()

    assert AssistantCloneEventRepository().list_clone_edges() == []


def test_a_self_edge_cannot_flag_an_assistant_as_its_own_duplicate(db: Session, engine: Engine) -> None:
    """A data glitch must not be able to produce a cleanup verdict."""
    from codemie.repository.assistants.assistant_clone_event_repository import (
        AssistantCloneEventRepository,
    )

    _record_clone(source_id=_SOURCE_ID, clone_id=_SOURCE_ID)

    assert AssistantCloneEventRepository().list_clone_edges() == []


def test_the_adapter_hands_the_edge_to_the_audit(db: Session) -> None:
    from codemie.service.marketplace_management.entity_adapter import get_adapter

    _record_clone()

    edges = get_adapter(EntityKind.ASSISTANT).list_clone_edges()

    assert (_SOURCE_ID, _CLONE_ID) in edges


def test_a_diverged_clone_does_not_force_a_duplicate_verdict(db: Session, engine: Engine) -> None:
    """The whole chain: a clone edge survives round-tripping, but no longer pins CRITICAL once reworked.

    The two seeded assistants share no tool, no MCP server, no skill, no
    datasource, no category and no name — their weighted structural score is
    0.0. MR !4070 review comment #23: a clone that has been substantially
    reworked since it was created must be judged on its current content, so
    the recorded edge alone must not resurrect a duplicate verdict here, even
    though `assistant_clone_event` still has the receipt.
    """
    from codemie.service.marketplace_management.audit_service import AuditCache

    _record_clone()

    cache = AuditCache()
    result = cache.run_audit(EntityKind.ASSISTANT, include_es_similarity=False)

    pair = result.similarity_results[_CLONE_ID].top_pair()
    assert pair is None or pair.severity is not Severity.CRITICAL, (
        "a diverged clone edge still forced a CRITICAL verdict — the override "
        "must be gated on current content similarity"
    )

    verdict = result.verdicts[_CLONE_ID]
    assert verdict.reason is None or verdict.reason.value != "duplicate", verdict

    # The edge itself must still be readable — only the forced severity is gone.
    from codemie.repository.assistants.assistant_clone_event_repository import (
        AssistantCloneEventRepository,
    )

    assert (_SOURCE_ID, _CLONE_ID) in AssistantCloneEventRepository().list_clone_edges()


def test_without_the_edge_the_same_two_assistants_are_not_duplicates(
    db: Session,
) -> None:
    """The control. If this fails, the test above proves nothing."""
    from codemie.service.marketplace_management.audit_service import AuditCache

    result = AuditCache().run_audit(EntityKind.ASSISTANT, include_es_similarity=False)

    assert result.similarity_results[_CLONE_ID].top_pair() is None
    verdict = result.verdicts[_CLONE_ID]
    assert verdict.reason is None or verdict.reason.value != "duplicate", verdict
