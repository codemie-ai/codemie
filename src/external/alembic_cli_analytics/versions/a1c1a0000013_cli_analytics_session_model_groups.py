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
"""CLI Analytics: the stored token groups of a session, the stamp that says they are trusted, and their kill-switch.

Revision ID: a1c1a0000013
Revises: a1c1a0000012
Create Date: 2026-11-11

One revision, two steps, with a commit between them (the shape of a1c1a0000012).

Step 1, under a lock wait bounded to 5 seconds, every statement idempotent (a column that exists is not altered: the
ALTER would ask for ACCESS EXCLUSIVE and fail behind a long reader on every retry):

- `session_model_groups`: one row per (session, raw model) with the rate-independent token sums that
  `cost_key_sql.model_token_groups` yields (the four base sums NOT NULL, the 5m / 1h cache-creation sums NULL when no
  row of the group carries a split), the normalised model name and `has_cost_daily` (whether `cost_daily` holds a row of
  the group). Tokens only: a rate change invalidates nothing. `fillfactor = 90` leaves room for in-place updates.
- `session_dims.model_groups_rev` (smallint, NULL, no default, not indexed here): the stamp of the stored groups. A
  session's rows are trusted when `rates_version IS NOT NULL AND model_groups_rev = 1`. Existing rows stay NULL.
- `session_sort_keys_control.model_groups_enabled` and `model_groups_operator_disabled` (boolean NOT NULL DEFAULT false):
  the kill-switch of the readers of the stored groups, separate from the one of the Sessions list.

Step 2: the partial index `session_dims_model_groups_pending` on the sessions whose groups are not trusted yet
(`rates_version IS NULL OR model_groups_rev IS DISTINCT FROM 1`), so the backfill and the readers find them without
scanning the table. A plain CREATE INDEX takes a SHARE lock on `session_dims` that blocks every writer for the build, so
above `CONCURRENT_ROWS` (150,000 rows, the threshold of the storage guide) the revision builds it CONCURRENTLY: it
commits, switches its connection to AUTOCOMMIT (CONCURRENTLY cannot run in a transaction), builds, and restores the
isolation level. Unlike a1c1a0000012, which leaves the pre-build to the operator, this revision does it itself; a
pre-built index of the same name is found by IF NOT EXISTS. An invalid leftover of a failed build is dropped first. The
advisory lock of the runner is session-level and is held through all of it. The concurrent build runs with no lock
wait bound: it blocks no one, and a bound would fail it behind any statement of the database older than its own.

The columns and the table are committed before step 2, so a failure of the index build leaves them in place; the
revision is not recorded and the next job runs it again.
The down_revision must be repointed to the head on main if another CLI analytics migration lands first.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "a1c1a0000013"
down_revision: str | None = "a1c1a0000012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN_LOCK_TIMEOUT = "5s"
INDEX_LOCK_TIMEOUT = "5s"
# CONCURRENTLY holds no lock that blocks a reader or a writer, but it waits for every transaction older than its own,
# anywhere in the database, and that wait is a lock wait: a bound would fail the build behind any long statement.
CONCURRENT_LOCK_TIMEOUT = "0"
CONCURRENT_ROWS = 150_000
TABLE = "session_model_groups"
CONTROL_TABLE = "session_sort_keys_control"
STAMP_COLUMN = "model_groups_rev"
CONTROL_COLUMNS = ("model_groups_enabled", "model_groups_operator_disabled")
PENDING_INDEX = "session_dims_model_groups_pending"

CREATE_TABLE = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    session_id               text    NOT NULL,
    model_name               text    NOT NULL,
    normalized_model_name    text    NOT NULL,
    input_tokens             bigint  NOT NULL,
    output_tokens            bigint  NOT NULL,
    cache_read_tokens        bigint  NOT NULL,
    cache_creation_tokens    bigint  NOT NULL,
    cache_creation_5m_tokens bigint,
    cache_creation_1h_tokens bigint,
    has_cost_daily           boolean NOT NULL,
    PRIMARY KEY (session_id, model_name)
) WITH (fillfactor = 90)"""

_PENDING = f"ON session_dims (session_id) WHERE rates_version IS NULL OR {STAMP_COLUMN} IS DISTINCT FROM 1"
CREATE_PENDING_INDEX = f"CREATE INDEX IF NOT EXISTS {PENDING_INDEX} {_PENDING}"
CREATE_PENDING_INDEX_CONCURRENTLY = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {PENDING_INDEX} {_PENDING}"
# At most CONCURRENT_ROWS + 1 rows are read, so the probe costs the same on a table of any size.
COUNT_ROWS = f"SELECT count(*) FROM (SELECT 1 FROM session_dims LIMIT {CONCURRENT_ROWS + 1}) s"

DOWNGRADE = [
    f"DROP INDEX IF EXISTS {PENDING_INDEX}",
    f"ALTER TABLE {CONTROL_TABLE} " + ", ".join(f"DROP COLUMN IF EXISTS {name}" for name in reversed(CONTROL_COLUMNS)),
    f"ALTER TABLE session_dims DROP COLUMN IF EXISTS {STAMP_COLUMN}",
    f"DROP TABLE IF EXISTS {TABLE}",
]


def drop_if_invalid(index: str) -> str:
    """A CREATE INDEX CONCURRENTLY that failed leaves an INVALID index under its name, and IF NOT EXISTS would skip it:
    the migration would report success while the planner ignores the index. Drop only an invalid one."""
    return (
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        f"WHERE c.relname = '{index}' AND pg_table_is_visible(c.oid) AND NOT i.indisvalid) "
        f"THEN DROP INDEX {index}; END IF; END $$"
    )


def add_column_if_missing(table: str, name: str, definition: str) -> str:
    """`ADD COLUMN IF NOT EXISTS` asks for ACCESS EXCLUSIVE even when the column exists, so under the 5 s lock wait it
    fails behind any long reader of the table, on every retry. Ask for the lock only when the column is missing."""
    return (
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_attribute "
        f"WHERE attrelid = '{table}'::regclass AND attname = '{name}' AND NOT attisdropped) "
        f"THEN ALTER TABLE {table} ADD COLUMN {name} {definition}; END IF; END $$"
    )


def _runner_lock_timeout() -> str:
    # Imported here: the runner imports Alembic, not the reverse.
    from codemie.repository.cli_analytics.postgres.migrations import LOCK_TIMEOUT

    return LOCK_TIMEOUT


def column_statements() -> list[str]:
    """Step 1: the bound on the lock wait, the table, the stamp column and the two control columns."""
    return [
        f"SET LOCAL lock_timeout = '{COLUMN_LOCK_TIMEOUT}'",
        CREATE_TABLE,
        add_column_if_missing("session_dims", STAMP_COLUMN, "smallint"),
        *[add_column_if_missing(CONTROL_TABLE, name, "boolean NOT NULL DEFAULT false") for name in CONTROL_COLUMNS],
    ]


def index_statements() -> list[str]:
    """Step 2 on a small table, in the transaction that follows the commit of step 1: the bound on the lock wait, the
    index (an invalid leftover dropped first), and the runner's wait restored."""
    return [
        f"SET LOCAL lock_timeout = '{INDEX_LOCK_TIMEOUT}'",
        drop_if_invalid(PENDING_INDEX),
        CREATE_PENDING_INDEX,
        f"SET LOCAL lock_timeout = '{_runner_lock_timeout()}'",
    ]


def _build_concurrently(bind) -> None:
    """Step 2 on a large table. The caller has committed, so no transaction is open and the isolation level may change.
    Session-level SET: SET LOCAL has no transaction to live in under AUTOCOMMIT."""
    bind.execution_options(isolation_level="AUTOCOMMIT")
    try:
        # The drop of an invalid leftover is a plain DROP INDEX (it cannot be CONCURRENTLY inside a DO block) and asks
        # for ACCESS EXCLUSIVE on the table: it keeps the bound, or it queues behind any long reader and every later
        # reader and writer of the table queues behind it.
        op.execute(f"SET lock_timeout = '{INDEX_LOCK_TIMEOUT}'")
        op.execute(drop_if_invalid(PENDING_INDEX))
        # Only the concurrent build is unbounded: it waits for older transactions anywhere, and a bound fails it.
        op.execute(f"SET lock_timeout = '{CONCURRENT_LOCK_TIMEOUT}'")
        op.execute(CREATE_PENDING_INDEX_CONCURRENTLY)
    finally:
        op.execute(f"SET lock_timeout = '{_runner_lock_timeout()}'")
        # SQLAlchemy autobegins a transaction object even under AUTOCOMMIT, and the level cannot change inside one.
        bind.commit()
        bind.execution_options(isolation_level=bind.dialect.default_isolation_level)


def upgrade() -> None:
    for statement in column_statements():
        op.execute(statement)
    # The table and the columns are what the readers need: they must outlive a failure of the index build below.
    bind = op.get_bind()
    bind.commit()
    large = (bind.execute(text(COUNT_ROWS)).scalar() or 0) > CONCURRENT_ROWS
    if not large:
        for statement in index_statements():
            op.execute(statement)
        return
    bind.commit()  # end the transaction the size probe started: CONCURRENTLY cannot run inside one
    _build_concurrently(bind)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
