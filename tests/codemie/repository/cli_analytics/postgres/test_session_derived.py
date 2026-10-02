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

"""Control flow of the session_dims columns derived in Python: feature_id and delivery_framework.

What the statements write is proven live (Task 17); these tests check only what is sent."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.postgres import session_derived
from codemie.repository.cli_analytics.postgres.session_derived import feature_id, update_session_derived


class DerivedConnection:
    """Serves the stored session_dims rows the derived update reads; records what it executes."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.fetched: list[tuple[object, ...]] = []
        self.executed: list[tuple[object, ...]] = []
        self.timeouts: list[float | None] = []

    async def fetch(self, sql: str, *args: object, timeout: float | None = None) -> list[dict[str, object]]:
        self.fetched.append((sql, *args))
        self.timeouts.append(timeout)
        return self.rows

    async def execute(self, sql: str, *args: object, timeout: float | None = None) -> str:
        self.executed.append((sql, *args))
        self.timeouts.append(timeout)
        return "UPDATE 1"


class ClassifierSpy:
    def __init__(self, label: str = "SDLC Factory") -> None:
        self.label = label
        self.calls: list[list[str]] = []

    def __call__(self, skill_names: list[str]) -> str:
        self.calls.append(skill_names)
        return self.label


def _row(session_id: str, branch_dominant: str | None, skill_names: list[str] | None) -> dict[str, object]:
    return {"session_id": session_id, "branch_dominant": branch_dominant, "skill_names": skill_names}


def test_feature_id_strips_the_first_path_segment() -> None:
    # The branch without its first path segment, NULL stays NULL.
    assert feature_id("feature/ABC-1-x") == "ABC-1-x"
    assert feature_id("main") == "main"
    assert feature_id(None) is None


@pytest.mark.asyncio
async def test_derived_update_without_classifier_does_not_write_delivery_framework() -> None:
    # The command-line backfill has no classifier: feature_id is written, delivery_framework never.
    conn = DerivedConnection([_row("s1", "feature/ABC-1-x", ["sdlc-factory:sdlc-standard"])])

    await update_session_derived(conn, ["s1"], None, 1.0)  # type: ignore[arg-type]

    assert len(conn.executed) == 1
    sql, *args = conn.executed[0]
    assert sql == session_derived._WRITE_FEATURE_ID
    assert "delivery_framework" not in sql
    assert args == [["s1"], ["ABC-1-x"]]


@pytest.mark.asyncio
async def test_derived_update_does_not_call_classifier_without_skill_names() -> None:
    # The classifier answers a default label for an empty list: a session with no skill name keeps
    # delivery_framework NULL, so the classifier is not asked about it.
    spy = ClassifierSpy()
    conn = DerivedConnection(
        [
            _row("s1", "main", None),
            _row("s2", "feature/X-2", []),
            _row("s3", None, ["sdlc-factory:sdlc-standard", "brainstorming"]),
        ]
    )

    await update_session_derived(conn, ["s1", "s2", "s3"], spy, 1.0)  # type: ignore[arg-type]

    assert spy.calls == [["sdlc-factory:sdlc-standard", "brainstorming"]]
    sql, *args = conn.executed[0]
    assert sql == session_derived._WRITE_DERIVED
    # s1: main -> main, no skills; s2: feature/X-2 -> X-2, no skills; s3: no branch, classified.
    assert args == [["s1", "s2", "s3"], ["main", "X-2", None], [None, None, "SDLC Factory"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("classify", [None, ClassifierSpy()], ids=["feature_id only", "with classifier"])
async def test_every_statement_of_the_derived_update_carries_the_callers_timeout(
    classify: ClassifierSpy | None,
) -> None:
    # The read and either write run inside the refresher's transaction: with the pool's default limit a
    # slow one would end as a client TimeoutError, which the refresher does not defer or drop a key for.
    conn = DerivedConnection([_row("s1", "feature/ABC-1-x", ["sdlc-factory:sdlc-standard"])])

    await update_session_derived(conn, ["s1"], classify, 305.0)  # type: ignore[arg-type]

    assert len(conn.fetched) == 1 and len(conn.executed) == 1
    assert conn.timeouts == [305.0, 305.0]  # the read, then the write


@pytest.mark.asyncio
async def test_a_failing_classifier_keeps_the_stored_label_and_does_not_stop_the_update() -> None:
    # The classifier is customer configuration. An exception from it must not leave the refresher's
    # transaction: the session gets no label this time, and every feature_id is still written.
    def classify(skill_names: list[str]) -> str:
        if skill_names == ["broken"]:
            raise ValueError("bad rule")
        return "SDLC Factory"

    conn = DerivedConnection(
        [_row("s1", "feature/ABC-1", ["broken"]), _row("s2", "feature/ABC-2", ["sdlc-factory:sdlc-standard"])]
    )

    with patch.object(session_derived, "logger") as logger:
        await update_session_derived(conn, ["s1", "s2"], classify, 1.0)  # type: ignore[arg-type]

    sql, *args = conn.executed[0]
    assert sql == session_derived._WRITE_DERIVED
    assert args == [["s1", "s2"], ["ABC-1", "ABC-2"], [None, "SDLC Factory"]]
    logger.exception.assert_called_once()
    assert "'s1'" in logger.exception.call_args.args[0]


def test_without_a_summary_the_classifier_reads_the_skills_of_otel_and_of_hooks() -> None:
    # session_skills holds only the skills OTel reported. A session known from hooks alone has its
    # skills in the `skills` fallback of session_dims: read without it, such a session would stay
    # without a label until its summary arrives.
    read = " ".join(session_derived._READ.split())
    skills = session_derived._SKILLS_OBJECT
    assert skills == "CASE WHEN jsonb_typeof(sd.skills) = 'object' THEN sd.skills END"  # never the keys of a non-object
    with_summary, without_summary = read.split("CASE WHEN sd.summary_ts IS NOT NULL THEN", 1)[1].split(" ELSE ", 1)
    assert "session_skills" not in with_summary
    assert f"FROM jsonb_object_keys({skills}) s (name) WHERE s.name <> ''" in with_summary
    assert (
        "FROM (SELECT ss.skill_name AS name FROM session_skills ss WHERE ss.session_id = sd.session_id "
        f"UNION SELECT s.name FROM jsonb_object_keys({skills}) s (name)) x WHERE x.name <> ''"
    ) in without_summary
