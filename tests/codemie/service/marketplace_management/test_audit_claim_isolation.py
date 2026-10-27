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

"""The marketplace slice must not inherit another package's database binding.

BLOCKER-CI-1. The marketplace tests were order-dependent: 388 green when the
slice ran alone, and red the moment the whole of `tests/codemie/` ran, with two
different faces on one cause.

    tests/codemie/service/marketplace_management/test_es_similarity_timeout.py
        ::test_audit_run_re_arms_the_circuit
    AuditAlreadyRunningError: An audit of ASSISTANT is already running
    (run 903a6307-78a5-42f8-8ddf-e6331bc861c2, started by EPLTVILW1190/59862)

The runner id in that message is *this pytest process*, which is the tell: the
"other pod" holding the claim was an earlier module of the same run.

Two independent defects had to line up:

1. `tests/codemie/marketplace_fixtures/conftest.py` bound
   `PostgresClient.get_engine` to a real, alembic-migrated PostgreSQL from a
   fixture declared `scope="session"`. A session-scoped generator fixture is
   finalised at the end of the *run*, not at the end of its package, so the
   binding stayed on for every package collected afterwards. Modules that
   reasoned "`PG_URL` points at a dead port and the session is a MagicMock
   here" were quietly talking to a live database.

2. Under that binding, `AuditCache.start_audit` / `run_audit` stop being dict
   mutations: they INSERT a `RUNNING` row into `mm_audit_run`, which holds the
   partial unique index `uq_mm_audit_run_active` until `_persist` closes it.
   Tests that stub `run_audit` or `_persist` can never close it, so the claim
   leaked and the next module to audit ASSISTANT was correctly refused.

Both are fixed at source (package-scoped binding; the claiming tests either use
a disposable database with a releasing teardown or take no claim at all). This
module is the guard that keeps them fixed. It sorts first in the package, so it
reports the state the marketplace slice *starts* in, before any of its own
fixtures have bound anything.

It is deliberately not a "no RUNNING rows anywhere" assertion: the point is not
that the table is clean, it is that this package is not silently running
against somebody else's database.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from codemie.clients.postgres import PostgresClient

_SCHEMA = "codemie"
# The statuses `uq_mm_audit_run_active` indexes: a row in either one holds the
# claim and refuses every later audit of that entity kind.
_ACTIVE = ("RUNNING", "QUEUED")


def _held_claims(engine: Engine) -> list[dict]:
    """Every active audit-run claim visible on *engine*, best effort."""
    try:
        with engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    text(
                        "select id, entity_kind, status, runner_id, started_at "
                        f"  from {_SCHEMA}.mm_audit_run "
                        f" where status in {_ACTIVE}"
                    )
                ).mappings()
            ]
    except Exception as exc:  # noqa: BLE001 - the binding is the headline
        return [{"id": "<unreadable>", "error": repr(exc)}]


def test_no_other_package_left_a_database_bound_to_this_one() -> None:
    """`get_engine()` must still be the unit suite's MagicMock at package start.

    `tests/conftest.py` installs a session-scoped autouse MagicMock over
    `PostgresClient.get_engine` precisely so that importing a module cannot
    open a connection. Packages that need a real database are expected to bind
    one for their own scope and hand it back. If a real `Engine` is bound here,
    some other package's binding outlived it, and every module in this package
    is now writing to a database it never asked for -- including the audit-run
    claims that made this slice order-dependent.
    """
    engine = PostgresClient.get_engine()

    if isinstance(engine, Engine):
        url = engine.url.render_as_string(hide_password=True)
        claims = _held_claims(engine)
        raise AssertionError(
            "A real database is bound to PostgresClient.get_engine at the start "
            f"of tests/codemie/service/marketplace_management/: {url}\n"
            "No fixture in this package has run yet, so the binding leaked out "
            "of an earlier package -- check for a fixture that patches "
            "get_engine with scope='session' instead of scope='package'.\n"
            "Audit-run claims currently held on that database (each one refuses "
            f"every later audit of its entity kind): {claims}\n"
            "This is BLOCKER-CI-1: it is what turned "
            "test_es_similarity_timeout.py::test_audit_run_re_arms_the_circuit "
            "red with AuditAlreadyRunningError, and "
            "test_marketplace_management_smoke.py::test_audit_run_is_accepted_"
            "with_202 red with 409, depending on which order the suite ran in."
        )
