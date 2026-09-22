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

"""create litellm_spend_upload_state

Revision ID: d8e4f1a2b6c3
Revises: a7d4c2b91f83
Create Date: 2026-09-09 00:00:00.000000

The uploader's durable copy position: one row per stream - `feed` for the scheduled run,
`adhoc:<since>/<until>` for each historical-load chunk, so parallel chunks track their own progress and
never contend for one row. Designed by the uploader team (`sql/01-codemie-upload-state.sql` in their
repository) and carried here because CodeMie owns every object in its schema and the uploader holds no
DDL privilege (uploader guide §2).

**Why a table and not an index.** The uploader used to derive its position on every run with
`SELECT max(start_time) FROM litellm_spend_logs`. No index on the ledger can serve that read: the primary
key leads with `request_id`, the only `start_time`-leading index (`ix_spend_logs_unattributed`) is partial,
and every other index has `start_time` trailing an unconstrained column, so PostgreSQL cannot take its
MIN/MAX shortcut and scans every partition once a minute - seconds after a month of rows, tens of seconds
after several. An index on `(start_time)` would make that read O(1) but would be a seventh
index-maintenance operation on every inserted row at the expected 2M rows a day, and on a partitioned
parent it cannot be built `CONCURRENTLY` (PD-11). One row written per page of copied rows, inside the
transaction that page already commits, is ~0.02-0.1% overhead where the index would be ~15-20%. The
backend never issues that read, so nothing here serves a CodeMie query.

**The columns are the uploader's contract, not this repository's design.** `stream` is the arbiter of its
`ON CONFLICT (stream) DO UPDATE`, so it must be the primary key. `watermark` is `timestamptz` because the
reader converts it with `astimezone` - a naive value would be read in the client's zone and silently shift
the feed - and it only ever moves forward (`GREATEST(stored, new)` on the uploader's side: a page whose
newest row is a late long-running call can sit below the stored value, and moving the position back would
only widen every following re-read). `rows_delivered` counts rows *delivered*, re-deliveries included - a
progress figure, not an audit of changes; during the feed's overlap re-read most deliveries change nothing,
and a counter that only moved on change would read as no progress at all. The two defaults are relied on by
the seed below and by the uploader's documented manual recovery insert, both of which omit those columns.

**Idempotent with the team's hand script, by design.** `sql/01` creates the same table for a database this
migration has not reached; `IF NOT EXISTS` and `ON CONFLICT DO NOTHING` let the two be applied in either
order without failing a deploy.

**Seeded from the ledger, once.** The `feed` row starts at the ledger's newest `start_time`, so the first
run continues from where the feed already stands instead of restarting the copy at the source's oldest
retained row. This is the one full scan of the ledger, and it happens here. `HAVING` drops the all-NULL
group: an empty ledger seeds nothing and the first run performs the historical load as designed.

**No GRANT, like the ledger's own migration.** The uploader's role is a deployment fact - the DSN it
connects with - not a migration constant; it needs `SELECT`, `INSERT` and `UPDATE` on this table and on the
ledger, and no DDL on either. **No model** under `codemie/repository`: the backend never reads this table.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e4f1a2b6c3"
down_revision: Union[str, None] = "a7d4c2b91f83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "litellm_spend_upload_state"
LEDGER = "litellm_spend_logs"
#: The scheduled run's stream. Historical-load chunks write their own `adhoc:<since>/<until>` rows.
FEED_STREAM = "feed"


def upgrade() -> None:
    # Verbatim from the uploader team's sql/01-codemie-upload-state.sql, the GRANT excepted.
    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            stream         text        NOT NULL PRIMARY KEY,
            watermark      timestamptz NOT NULL,
            rows_delivered bigint      NOT NULL DEFAULT 0,
            updated_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO {TABLE} (stream, watermark)
        SELECT '{FEED_STREAM}', max(start_time) FROM {LEDGER}
        HAVING max(start_time) IS NOT NULL
        ON CONFLICT (stream) DO NOTHING
        """
    )


def downgrade() -> None:
    # Loses the feed's position. The uploader recovers it with one max(start_time) scan and a WARNING,
    # then rewrites the row on its first committed page.
    op.execute(f"DROP TABLE {TABLE}")
