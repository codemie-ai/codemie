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
"""CLI Analytics: the activity facts of a session and the six session_dims indexes of the Sessions list.

Revision ID: a1c1a0000011
Revises: a1c1a0000010
Create Date: 2026-11-06

One revision, two steps, with a commit between them.

Step 1, the columns: `has_activity` ("a row exists in any of the five activity tables"), `last_activity_day` (the
latest such day) and `resumed` ("a row exists on a UTC day other than the session's start day"); see
`session_activity.py`. All three are NULL until derived, and while a session is NULL the reader asks the rollup tables,
so the lists are correct with only these columns: the indexes make them fast, not right. The ADD COLUMNs are
metadata-only and quick, and every Sessions reader needs them. No default, so nothing is rewritten. ALTER TABLE takes
an ACCESS EXCLUSIVE lock that queues every reader behind it: the wait for it is bounded to 5 seconds, not the runner's.

The columns are COMMITTED before step 2 (`op.get_bind().commit()`, the runner lets the revision end its own
transaction: `migrations._upgrade_to`). A lock timeout of the slow index builds then rolls back the indexes only; the
revision is not recorded, the next job runs it again, and both steps are idempotent (IF NOT EXISTS), so the readers
never fail on a missing column while the indexes retry.

Step 2, six indexes on `session_dims` over those columns. The Sessions window rule admits
a session that started in the window and has ever produced an activity row, or that has activity in the window;
asked of the five rollup tables for each of ~10^6 sessions that is a scan of the whole window, stored on the session
it is an index range. The refresher derives the facts for every session it touches and the maintenance job derives
the sessions that existed before this revision, a bounded batch per run (`session_activity.py`).

`session_dims_started_at` (started_at) orders the `start_time` page of any scope's window readers that do not filter
by activity. `session_dims_started_shared` (started_at) WHERE <the session is not owner-private> holds the sessions
an admin's shared (`exclude`) list can show; its predicate is the SQL spelling of `scope.sql_private` over the stored
columns, written out here because a migration must not change with the code. The framework counts read it.
The four indexes over the facts are partial: `session_dims_active_started` (started_at WHERE has_activity) serves the
page of any scope; `session_dims_active_shared_cov` (started_at INCLUDE (session_id) WHERE has_activity AND <the shared
predicate>) serves the unfiltered shared list's page, count and ids-only statements as index-only scans;
`session_dims_resumed_last` (last_activity_day WHERE resumed) finds the sessions started before a window and active in
it; `session_dims_activity_pending` (started_at WHERE has_activity IS NULL) finds the underived sessions, and is empty
once every session is derived. The first three start empty and fill as the facts are derived; the last starts with every
row and shrinks.
Write cost: the predicates read `subscription_type` and `project_origin`, which the session binding writes once, when a
session is bound; the refresher and ingest never update them per event. The refresher sets the facts once per session
and again only when its activity days change, a non-HOT update (they are indexed). The backfill rewrites each
pre-existing row once: see the storage guide for the vacuum that follows it.
Plain CREATE INDEX: it takes a SHARE lock on session_dims, so the wait for that lock is bounded to 5 seconds (once, for
all statements) and a failed attempt is retried by the next job. Above the size threshold in the storage guide,
pre-build each index CONCURRENTLY under the same name so these statements find it and do nothing. An invalid index of
that name (a failed CONCURRENTLY pre-build) is dropped first and rebuilt.
The down_revision must be repointed to the head on main if another CLI analytics migration lands first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000011"
down_revision: str | None = "a1c1a0000010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN_LOCK_TIMEOUT = "5s"
ADD_COLUMNS = (
    "ALTER TABLE session_dims ADD COLUMN IF NOT EXISTS has_activity boolean, "
    "ADD COLUMN IF NOT EXISTS last_activity_day date, ADD COLUMN IF NOT EXISTS resumed boolean"
)
COLUMNS_DOWNGRADE = [
    "ALTER TABLE session_dims DROP COLUMN IF EXISTS resumed, DROP COLUMN IF EXISTS last_activity_day, "
    "DROP COLUMN IF EXISTS has_activity"
]

INDEX_LOCK_TIMEOUT = "5s"

STARTED_AT_INDEX = "session_dims_started_at"
SHARED_INDEX = "session_dims_started_shared"
ACTIVE_INDEX = "session_dims_active_started"
ACTIVE_COVERING_INDEX = "session_dims_active_shared_cov"
RESUMED_INDEX = "session_dims_resumed_last"
PENDING_INDEX = "session_dims_activity_pending"

_WS = "E' \\t\\n\\r\\f\\013'"
_ORIGIN = f"lower(btrim(coalesce(project_origin, ''), {_WS}))"
_SUBSCRIPTION = f"lower(btrim(coalesce(subscription_type, ''), {_WS}))"
_SHARED_ORIGINS = "'linked_folder', 'profile', 'registered_repository', 'default_project', 'legacy_project'"
# NOT sql_private(...): the session is not owner-private.
SHARED_PREDICATE = (
    f"started_at IS NOT NULL AND NOT ({_ORIGIN} = 'personal' OR "
    f"({_SUBSCRIPTION} = 'personal' AND {_ORIGIN} NOT IN ({_SHARED_ORIGINS})))"
)

CREATE_STARTED_AT = f"CREATE INDEX IF NOT EXISTS {STARTED_AT_INDEX} ON session_dims (started_at)"
CREATE_SHARED = f"CREATE INDEX IF NOT EXISTS {SHARED_INDEX} ON session_dims (started_at) WHERE {SHARED_PREDICATE}"
CREATE_ACTIVE = f"CREATE INDEX IF NOT EXISTS {ACTIVE_INDEX} ON session_dims (started_at) WHERE has_activity"
CREATE_ACTIVE_COVERING = (
    f"CREATE INDEX IF NOT EXISTS {ACTIVE_COVERING_INDEX} ON session_dims (started_at) INCLUDE (session_id) "
    f"WHERE has_activity AND {SHARED_PREDICATE}"
)
CREATE_RESUMED = f"CREATE INDEX IF NOT EXISTS {RESUMED_INDEX} ON session_dims (last_activity_day) WHERE resumed"
CREATE_PENDING = f"CREATE INDEX IF NOT EXISTS {PENDING_INDEX} ON session_dims (started_at) WHERE has_activity IS NULL"

# In the order they are built; the downgrade drops them in reverse, before the columns.
INDEXES = (
    (STARTED_AT_INDEX, CREATE_STARTED_AT),
    (SHARED_INDEX, CREATE_SHARED),
    (ACTIVE_INDEX, CREATE_ACTIVE),
    (ACTIVE_COVERING_INDEX, CREATE_ACTIVE_COVERING),
    (RESUMED_INDEX, CREATE_RESUMED),
    (PENDING_INDEX, CREATE_PENDING),
)

INDEXES_DOWNGRADE = [f"DROP INDEX IF EXISTS {name}" for name, _ in reversed(INDEXES)]


def drop_if_invalid(index: str) -> str:
    """A CREATE INDEX CONCURRENTLY that failed leaves an INVALID index under its name, and IF NOT EXISTS would skip it:
    the migration would report success while the planner ignores the index. Drop only an invalid one."""
    return (
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        f"WHERE c.relname = '{index}' AND pg_table_is_visible(c.oid) AND NOT i.indisvalid) "
        f"THEN DROP INDEX {index}; END IF; END $$"
    )


def column_statements() -> list[str]:
    """Step 1: the bound on the lock wait and the columns."""
    return [f"SET LOCAL lock_timeout = '{COLUMN_LOCK_TIMEOUT}'", ADD_COLUMNS]


def index_statements() -> list[str]:
    """Step 2, in the transaction that follows the commit of the columns: the bound on the lock wait, each index
    (an invalid leftover dropped first), and the runner's wait restored."""
    # Restore the runner's lock wait from its constant. Imported here: the runner imports Alembic, not the reverse.
    from codemie.repository.cli_analytics.postgres.migrations import LOCK_TIMEOUT

    statements = [f"SET LOCAL lock_timeout = '{INDEX_LOCK_TIMEOUT}'"]
    for name, create in INDEXES:
        statements += [drop_if_invalid(name), create]
    statements.append(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    return statements


def upgrade() -> None:
    for statement in column_statements():
        op.execute(statement)
    # The columns are what every reader needs: they must outlive a lock timeout of the index builds below. The
    # runner's transaction is the connection's own (`engine.connect()`), so this ends it and the next statement
    # starts the one of step 2; the advisory lock of the runner is session-level and stays held.
    op.get_bind().commit()
    for statement in index_statements():
        op.execute(statement)


def downgrade() -> None:
    for statement in [*INDEXES_DOWNGRADE, *COLUMNS_DOWNGRADE]:
        op.execute(statement)
