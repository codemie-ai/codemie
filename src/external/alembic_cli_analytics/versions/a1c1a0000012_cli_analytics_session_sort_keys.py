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
"""CLI Analytics: the stored sort keys of a session, the kill-switch of the readers that use them, and the index of the framework window.

Revision ID: a1c1a0000012
Revises: a1c1a0000011
Create Date: 2026-11-05

One revision, two steps, with a commit between them (the shape of a1c1a0000011).

Step 1, the columns and the control table. Seven nullable columns on `session_dims`, none with a default:
`cache_read_total` and `api_call_total` (the inputs of the context-usage sort key), `classifier_names` (the session's
classifier names, ordered), `models_used` and `kind_masks` (the priced models and, per model, the token kinds used) for
the framework grouping, `cost_key` (the cost sort key) and `rates_version`. `rates_version` is the per-session stamp:
non-NULL means the session's stored columns are derived, and for `cost_key` it names the rate set that priced it. All
seven are NULL until the refresher or the maintenance job derives them; while a session is NULL the readers compute it
from the rollup tables, so the lists are correct at every point of the roll-out. Adding nullable columns without a
default rewrites nothing.

`session_sort_keys_control` holds one row: `enabled`, false until the stored readers are on, and `operator_disabled`,
true after an explicit `sort_keys_cli disable`. The maintenance job turns `enabled` on by itself once no session lacks
a usable stamp (none with `rates_version` NULL or different from the current rates version), unless
`operator_disabled` is set; `sort_keys_cli enable` sets `enabled` and clears `operator_disabled`, `disable` clears
`enabled` and sets `operator_disabled`, so an explicit disable sticks. It is the kill-switch that sends every request
back to the rollup statements.

The lock wait of the DDL is bounded to 5 seconds. ALTER TABLE takes an ACCESS EXCLUSIVE lock that queues every reader
behind it. The columns are COMMITTED before step 2 (`op.get_bind().commit()`, the runner lets the revision end its own
transaction: `migrations._upgrade_to`). A lock timeout of the index build then rolls back the index only; the revision is
not recorded, the next job runs it again, and both steps are idempotent (IF NOT EXISTS), so the readers never fail on a
missing column while the index retries.

Step 2, two indexes on `session_dims`; the first is partial, `WHERE has_activity AND <the session is not owner-private>`: the sessions
of the started arm of the Sessions window that the shared (`exclude`) list can show, the predicate of
`session_dims_active_shared_cov` (a1c1a0000011) with its `SHARED_PREDICATE` copied byte for byte (a migration must not
change with the code). The other arms of the window (underived, resumed, edge day) are small and read `session_dims`
through the indexes of a1c1a0000011.

- `session_dims_fw_win`: `started_at`, INCLUDEing the id, the stamp and every stored column the Sessions list reads
  (`classifier_names`, `cache_read_total`, `api_call_total`, `cost_key`, `models_used`, `kind_masks`), so the window
  of the framework, ctx and cost pages is read from this index alone, without visiting `session_dims`.
- `session_dims_rates_version`: the stamp alone. The sessions without a usable stamp (set S) are read from it, so a
  request does not scan the window or the table to find them.

Three more indexes were designed and are NOT built: a ctx-key walk, a cost-key walk and a ctx window index. The
benchmark of 2026-11-06 (300k sessions, every request type at 7/30/90 days, stored on and off) recorded `idx_scan` 0
for each of them: the statements sort the merged key (stored and live), so the planner never walks them in order.
Only hand-written probe queries could use them, at about 270 MB per 300k rows and a HOT ratio of 0.0.

Write cost: the refresher updates these columns on `session_dims` after the rollup statements. An UPDATE is a HOT
update only when it changes no indexed column and the page has room for the new row version. Every stored column
is an INCLUDE column of `session_dims_fw_win` (and `rates_version` also has its own index), so changing
any of them is never a HOT update, and an UPDATE that rewrites a row to the same values only stays HOT when the
page has room. The wider index costs 25-45% more time and 23-28% more WAL on a rewrite of 20,000 sessions than the
narrow one (measured; see the merge request). The backfill rewrites each pre-existing row once, and the wider rows fit
fewer to a page; see the storage guide for the vacuum that follows it. The cost is accepted over a fillfactor or a
side table.
Size limit: a btree entry is at most about 2.7 kB, and `classifier_names` and `models_used` come from names the client
sends. The derivation does not store the arrays of a session whose two arrays exceed 2300 bytes: it leaves that session
unstamped and puts a marker name in `classifier_names` (`session_sort_keys.OVERSIZED_MARK`), so the readers compute it
live and the backfill, the re-price and the automatic enable skip it. That cap is code, not schema.
Plain CREATE INDEX: it takes a SHARE lock on session_dims, so the wait for that lock is bounded to 5 seconds and a
failed attempt is retried by the next job. Above the size threshold in the storage guide, pre-build the index
CONCURRENTLY under the same name so these statements find it and do nothing. An invalid index of that name (a failed
CONCURRENTLY pre-build) is dropped first and rebuilt.
The down_revision must be repointed to the head on main if another CLI analytics migration lands first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000012"
down_revision: str | None = "a1c1a0000011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN_LOCK_TIMEOUT = "5s"
INDEX_LOCK_TIMEOUT = "5s"
CONTROL_TABLE = "session_sort_keys_control"
COLUMNS = (
    ("cache_read_total", "bigint"),
    ("api_call_total", "bigint"),
    ("classifier_names", "text[]"),
    ("models_used", "text[]"),
    ("kind_masks", "int[]"),
    ("cost_key", "numeric"),
    ("rates_version", "text"),
)

CREATE_CONTROL = (
    f"CREATE TABLE IF NOT EXISTS {CONTROL_TABLE} "
    "(enabled boolean NOT NULL DEFAULT false, operator_disabled boolean NOT NULL DEFAULT false)"
)
SEED_CONTROL = f"INSERT INTO {CONTROL_TABLE} (enabled) SELECT false WHERE NOT EXISTS (SELECT 1 FROM {CONTROL_TABLE})"

FRAMEWORK_WINDOW_INDEX = "session_dims_fw_win"
RATES_VERSION_INDEX = "session_dims_rates_version"

# Copied from a1c1a0000011: the session is not owner-private.
_WS = "E' \\t\\n\\r\\f\\013'"
_ORIGIN = f"lower(btrim(coalesce(project_origin, ''), {_WS}))"
_SUBSCRIPTION = f"lower(btrim(coalesce(subscription_type, ''), {_WS}))"
_SHARED_ORIGINS = "'linked_folder', 'profile', 'registered_repository', 'default_project', 'legacy_project'"
SHARED_PREDICATE = (
    f"started_at IS NOT NULL AND NOT ({_ORIGIN} = 'personal' OR "
    f"({_SUBSCRIPTION} = 'personal' AND {_ORIGIN} NOT IN ({_SHARED_ORIGINS})))"
)
_PARTIAL = f"WHERE has_activity AND {SHARED_PREDICATE}"

CREATE_FRAMEWORK_WINDOW = (
    f"CREATE INDEX IF NOT EXISTS {FRAMEWORK_WINDOW_INDEX} ON session_dims (started_at) "
    f"INCLUDE (session_id, classifier_names, rates_version, cache_read_total, api_call_total, cost_key, "
    f"models_used, kind_masks) {_PARTIAL}"
)

# Every request reads the sessions without a usable stamp (set S) from here: `rates_version IS NULL` and, for the cost
# key, a version other than the current one (`<` or `>`). Equal values are stored once (deduplicated btree), so it is a
# few MB, and the reads are empty index ranges while every session is stamped. Setting a stamp to the value it
# already has is not a change of an indexed column, so a refresh of a stamped session stays eligible for a HOT update.
CREATE_RATES_VERSION = f"CREATE INDEX IF NOT EXISTS {RATES_VERSION_INDEX} ON session_dims (rates_version)"

# In the order they are built; the downgrade drops them in reverse, before the table and the columns.
INDEXES = (
    (FRAMEWORK_WINDOW_INDEX, CREATE_FRAMEWORK_WINDOW),
    (RATES_VERSION_INDEX, CREATE_RATES_VERSION),
)

DOWNGRADE = [
    *[f"DROP INDEX IF EXISTS {name}" for name, _ in reversed(INDEXES)],
    f"DROP TABLE IF EXISTS {CONTROL_TABLE}",
    "ALTER TABLE session_dims " + ", ".join(f"DROP COLUMN IF EXISTS {name}" for name, _ in reversed(COLUMNS)),
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


def column_statements() -> list[str]:
    """Step 1: the bound on the lock wait, the columns and the control table with its one row."""
    return [
        f"SET LOCAL lock_timeout = '{COLUMN_LOCK_TIMEOUT}'",
        *[add_column_if_missing("session_dims", name, sql_type) for name, sql_type in COLUMNS],
        CREATE_CONTROL,
        SEED_CONTROL,
    ]


def index_statements() -> list[str]:
    """Step 2, in the transaction that follows the commit of step 1: the bound on the lock wait, each index (an invalid
    leftover dropped first), and the runner's wait restored."""
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
    # The columns and the control table are what every reader needs: they must outlive a lock timeout of the index
    # build below. The runner's transaction is the connection's own (`engine.connect()`), so this ends it and the next
    # statement starts the one of step 2; the advisory lock of the runner is session-level and stays held.
    op.get_bind().commit()
    for statement in index_statements():
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
