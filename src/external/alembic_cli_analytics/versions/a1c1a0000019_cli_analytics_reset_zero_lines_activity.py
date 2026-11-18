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

"""CLI Analytics: repair session_dims.has_activity for sessions derived under the old, value-blind lines_daily rule.

`session_activity.DERIVE_SQL` (EPMCDME-16496 AC4) made `has_activity`/`last_activity_day` value-aware for
`lines_daily`: a day row whose `lines_added` and `lines_removed` are both 0 is not "real" activity any more. That
only changes what a *future* derive computes. A session the refresher already derived under the old rule — one
whose only activity-table row, ever, is a zero-value `lines_daily` row — keeps `has_activity = true` forever: the
refresher only re-derives a session when its rollup rows change, `backfill_session_activity` only picks up rows
still NULL (`session_activity._PENDING_SQL`), and neither condition holds for a quiet, already-derived session. Such
a session is wrongly admitted through arm 1 of `_admitted_arms` (a whole-day window trusts the stored facts; see
`reader.py`) until it next gets real activity, which re-derives it from scratch anyway.

No DDL, so no column, index or lock concerns beyond the row locks of the UPDATE itself. One DML statement, run in
batches of RESET_BATCH session ids, each its own transaction (as the backfill loop of a1c1a0000018), so no lock is
held for long and a retry continues where a failed run left off: a row already reset no longer has `has_activity`
true, so the WHERE clause skips it on the next batch or the next run. Driven by `has_activity`, not by `FOR UPDATE
SKIP LOCKED`: a session the refresher is concurrently re-deriving for real new activity either loses the race (this
statement waits for its row lock, then the WHERE no longer matches) or wins it and gets reset moments before the
refresher's own write lands — either way the result is the refresher's, and at worst a session the refresher just
fixed is picked up by the maintenance backfill once more.

Resetting only `has_activity`/`last_activity_day`, never `resumed`: `resumed` stays value-blind over every row on
purpose (it answers "did this session continue past its start day, however", not "does it have real activity"), so
the old rule never made it wrong.

Revision ID: a1c1a0000019
Revises: a1c1a0000018
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

revision: str = "a1c1a0000019"
down_revision: str | None = "a1c1a0000018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors the rollup refresher's default batch size.
RESET_BATCH = 5000
RESET = """
UPDATE session_dims sd SET has_activity = NULL, last_activity_day = NULL
WHERE sd.session_id IN (
    SELECT t.session_id FROM session_dims t
    WHERE t.has_activity
      AND EXISTS (SELECT 1 FROM lines_daily l WHERE l.session_id = t.session_id)
      AND NOT EXISTS (
          SELECT 1 FROM lines_daily l
          WHERE l.session_id = t.session_id AND (l.lines_added <> 0 OR l.lines_removed <> 0)
      )
      AND NOT EXISTS (SELECT 1 FROM turns_daily td WHERE td.session_id = t.session_id)
      AND NOT EXISTS (SELECT 1 FROM tool_facts_daily tf WHERE tf.session_id = t.session_id)
      AND NOT EXISTS (SELECT 1 FROM cost_daily c WHERE c.session_id = t.session_id)
      AND NOT EXISTS (SELECT 1 FROM session_usage u WHERE u.session_id = t.session_id)
    LIMIT :batch)
"""

UPGRADE = [RESET]


def upgrade() -> None:
    # Imported here: the runner imports Alembic, not the reverse (as a1c1a0000011, a1c1a0000018).
    from codemie.repository.cli_analytics.postgres.migrations import LOCK_TIMEOUT

    bind: Connection = op.get_bind()
    while True:
        op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        reset = bind.execute(text(RESET), {"batch": RESET_BATCH}).rowcount
        bind.commit()
        if reset < RESET_BATCH:
            break


def downgrade() -> None:
    """No-op: which sessions were reset, and what they held before, is not recoverable. The maintenance backfill
    will have re-derived them under the current rule by the time a downgrade could run anyway."""
