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

"""Merging the A5 framework sessions counted in Postgres into the Elasticsearch session rows, in the worker."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from codemie.service.usage_attribution.fetch import Spec

logger = logging.getLogger(__name__)


def merge_postgres_framework(job: dict[str, Any], sess_spec: Spec, people: list[list[Any]]) -> bool:
    """Raise the framework counts of the A5 rows with the sessions counted in Postgres (``pg_framework_rows`` of the
    job, matched by person and repository). ``people`` are the rows of ``app_db.load_people`` covering the people of
    the session rows (the email is the ninth column). Any failure keeps the Elasticsearch rows, logs the type of the
    exception (no personal data) and returns True, so the caller does not let the Elasticsearch-only rows stand for
    the hour; False when merged or when there was nothing to merge."""
    pg_rows = job.get("pg_framework_rows")
    if not pg_rows:
        return False
    from codemie.service.usage_attribution import framework_sessions

    try:
        email_by_uid = {str(p[0]): p[8] for p in people if p[8]}
        sess_spec.result = framework_sessions.merge_framework(sess_spec.result or [], pg_rows, email_by_uid)
    except Exception as exc:  # noqa: BLE001 - never fails the job
        logger.warning("A5 framework sessions not merged from Postgres: %s", type(exc).__name__)
        return True
    return False


def load_people_and_merge(
    engine: Any,
    job: dict[str, Any],
    by_key: dict[str, Spec],
    uids: set[str],
    fresh_evidence: bool,
    db_timings: list[dict],
) -> tuple[list[list[Any]], bool, float]:
    """Read the people of the analysis (``uids``) from the app DB and, when the evidence is read now and the job has
    Postgres rows, merge the sessions counted in Postgres into the session rows. The sessions are matched by the
    emails of the session rows' people, so one read covers both. Returns the people of the analysis, whether the merge
    failed (see ``merge_postgres_framework``) and the milliseconds spent."""
    from sqlmodel import Session

    from codemie.service.usage_attribution import app_db

    merge_pg = fresh_evidence and bool(job.get("pg_framework_rows"))
    # The session rows spec is among the specs only when the evidence is read now.
    session_uids = {r[0] for r in (by_key["sess"].result or [])} if merge_pg else set()
    t_people = time.perf_counter()
    with Session(engine) as session:
        loaded = app_db.load_people(session, sorted(uids | session_uids), db_timings, job.get("user_enrichment", True))
    people_ms = (time.perf_counter() - t_people) * 1000.0
    people = [p for p in loaded if str(p[0]) in uids] if session_uids - uids else loaded
    merge_failed = merge_pg and merge_postgres_framework(job, by_key["sess"], loaded)
    return people, merge_failed, people_ms
