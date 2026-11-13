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

"""CLI Analytics: sub-agent sessions are flagged in session_dims.

The session listing excludes a session whose id a parent names as agent_id in agent.subagent.start (a Copilot
client's start, see `hook_row_sql.names_subagent_session`; the rule is written out below because a migration must
not change with the code). The rollup sets session_dims.is_subagent from that event; the partial index serves its
lookup by agent_id.

Three steps, each in a transaction of its own, like a1c1a0000011 (the runner hands the revision a connection with a
transaction open, `migrations._upgrade_to`, so the revision ends it itself with `op.get_bind().commit()`):

1. The column. ALTER TABLE takes an ACCESS EXCLUSIVE lock that queues every reader behind it, so the wait for it is
   bounded to 5 seconds, not the runner's, and the column is COMMITTED before the slow steps: the readers never fail
   on a missing column while the index build or the backfill retries. IF NOT EXISTS: a retry meets the column.
2. The index, outside a transaction. hook_events is partitioned and PostgreSQL cannot build an index on a
   partitioned table CONCURRENTLY, so the index is built the way its documentation prescribes: the parent index ON
   ONLY (no scan), then each partition's index CONCURRENTLY, attached to the parent. Writes to hook_events are never
   blocked. A build that failed midway is repaired by running the upgrade again.
3. The backfill, after the ALTER's transaction is over: it is driven by the start events (the index of step 2), so
   it updates only the sessions they name, never every row of session_dims, in batches of BACKFILL_BATCH sessions, each
   committed on its own, so no lock is held for long and a retry continues where a failed run stopped. Idempotent:
   the rows already flagged are skipped.

Revision ID: a1c1a0000018
Revises: a1c1a0000017
Create Date: 2026-10-30
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

revision: str = "a1c1a0000018"
down_revision: str | None = "a1c1a0000017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "hook_events_subagent_start"
_INDEX_DEFINITION = "(agent_id) WHERE event_type = 'agent.subagent.start'"
# A partitioned table without partitions has nothing to scan, so its index is plain and valid at once.
PLAIN_INDEX = f"CREATE INDEX IF NOT EXISTS {INDEX} ON hook_events {_INDEX_DEFINITION}"
# With partitions the parent index is invalid until every partition's index is attached.
PARENT_INDEX = f"CREATE INDEX IF NOT EXISTS {INDEX} ON ONLY hook_events {_INDEX_DEFINITION}"
PARTITION_COUNT = "SELECT count(*) FROM pg_partition_tree(to_regclass('hook_events')) WHERE isleaf"
# Partitions with no index attached to the parent's yet, and the (quoted) name their index gets.
UNINDEXED_PARTITIONS = f"""
SELECT t.relid::regclass::text AS partition, quote_ident(left(c.relname, 47) || '_subagent_start') AS index_name
FROM pg_partition_tree(to_regclass('hook_events')) t
JOIN pg_class c ON c.oid = t.relid
WHERE t.isleaf
  AND NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_inherits h ON h.inhrelid = i.indexrelid
                  WHERE i.indrelid = t.relid AND h.inhparent = to_regclass('{INDEX}'))
ORDER BY c.relname
"""
# Passes over the partitions: a partition created while the first pass runs gets the parent's index itself.
_MAX_PASSES = 3

COLUMN_LOCK_TIMEOUT = "5s"
ADD_COLUMN = "ALTER TABLE session_dims ADD COLUMN IF NOT EXISTS is_subagent boolean"
# Sessions already rolled up; the raw start events still kept name their sub-agents. The rule of the start event:
# a Copilot client, a session of its own and an agent_id that is another session. One batch of the backfill: the
# sessions still to flag, so a batch that flags fewer than BACKFILL_BATCH is the last.
BACKFILL_BATCH = 5000
BACKFILL = """
UPDATE session_dims d SET is_subagent = true
WHERE d.session_id IN (
    SELECT t.session_id FROM session_dims t
    WHERE t.is_subagent IS DISTINCT FROM true
      AND t.session_id IN (SELECT sh.agent_id FROM hook_events sh
                           WHERE sh.event_type = 'agent.subagent.start' AND sh.session_id <> ''
                             AND coalesce(sh.agent_id, '') <> '' AND sh.agent_id <> sh.session_id
                             AND sh.attrs->>'platform' IN ('copilot-cli', 'copilot-desktop'))
    LIMIT :batch)
"""

# The revision's own statements, in order; the index is built between them (see `upgrade`).
UPGRADE = [ADD_COLUMN, BACKFILL]

DOWNGRADE = [
    f"DROP INDEX IF EXISTS {INDEX}",
    "ALTER TABLE session_dims DROP COLUMN IF EXISTS is_subagent",
]


def _build_index(bind: Connection) -> None:
    """Runs on an autocommit connection (CREATE INDEX CONCURRENTLY cannot run inside a transaction)."""
    if not bind.execute(text(PARTITION_COUNT)).scalar():
        bind.execute(text(PLAIN_INDEX))
        return
    bind.execute(text(PARENT_INDEX))
    for _ in range(_MAX_PASSES):
        partitions = bind.execute(text(UNINDEXED_PARTITIONS)).all()
        if not partitions:
            return
        for partition, index in partitions:
            # An unattached index of this name is the leftover of a build that failed midway (INVALID): rebuild it.
            bind.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {index}"))
            bind.execute(text(f"CREATE INDEX CONCURRENTLY {index} ON {partition} {_INDEX_DEFINITION}"))
            bind.execute(text(f"ALTER INDEX {INDEX} ATTACH PARTITION {index}"))
    if bind.execute(text(UNINDEXED_PARTITIONS)).first():
        raise RuntimeError(f"partitions of hook_events kept appearing while {INDEX} was built; run the upgrade again")


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(f"SET LOCAL lock_timeout = '{COLUMN_LOCK_TIMEOUT}'")
    op.execute(ADD_COLUMN)
    bind.commit()
    # No transaction is open now, so the isolation level may change; CREATE INDEX CONCURRENTLY needs autocommit.
    # The advisory lock of the runner is session-level and stays held.
    bind.execution_options(isolation_level="AUTOCOMMIT")
    try:
        _build_index(bind)
    finally:
        # Even under AUTOCOMMIT the statements above autobegan a SQLAlchemy Transaction object; it must end before the
        # isolation level may change (a no-op at the server: every statement is already committed).
        bind.commit()
        bind.execution_options(isolation_level=bind.default_isolation_level)
    # Imported here: the runner imports Alembic, not the reverse.
    from codemie.repository.cli_analytics.postgres.migrations import LOCK_TIMEOUT

    # The backfill runs in batches, each in a transaction of its own with the runner's lock wait restored (SET LOCAL
    # ends with the commit, as in a1c1a0000011): no long transaction, and a failure keeps the batches already done.
    while True:
        op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        flagged = bind.execute(text(BACKFILL), {"batch": BACKFILL_BATCH}).rowcount
        bind.commit()
        if flagged < BACKFILL_BATCH:
            break


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
