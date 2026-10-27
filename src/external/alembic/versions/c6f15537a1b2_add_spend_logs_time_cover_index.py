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

"""add_spend_logs_time_cover_index

Revision ID: c6f15537a1b2
Revises: f3e2d1c0b9a8
Create Date: 2026-10-05 00:00:00.000000

EPMCDME-15537: a covering, time-leading index on the ledger - access pattern P11, the
window-bounded aggregates. It reopens the table's original decision to carry no general
`start_time` index, with the owner's approval, and supersedes the premise of `d8e4f1a2b6c3`
(`create_litellm_spend_upload_state`) that the ledger needs no `start_time`-leading index, now that
an access pattern needing one was measured; that historical migration is left as written.
Hand-written for the same reason as the table's own migration (`l1t2l3m5s6p7`): the model is
deliberately absent from `env.py`.

* **The access pattern.** Every window-bounded aggregate - the summary groups, the traffic-class
  and time-bucket sums, and the dimension and project residues in
  `codemie/repository/spend_logs_repository.py` - is filtered by `start_time` alone. With no
  `start_time`-leading index each was a parallel full scan of the month partition: 41.9 s for
  the default 30-day window over 14.5M rows (EXPLAIN ANALYZE, 856k pages read). With this index
  the planner chooses an index-only scan: the default-window summary takes 1.1 s, and the
  Analytics page money endpoints went from 45-50 s to 4.6-6.7 s under one concurrent page load.
* **`INCLUDE` covers the unscoped reads, not every read.** It lists the columns the summary-group,
  traffic-class, time-bucket, workflow/execution and project-residue reads filter or sum - the
  default Analytics page for an unrestricted caller - so those never visit the heap. Model-dimension
  reads (`model_group`) and access-scoped or report-filtered reads (`attr_form`, `attr_user_id`,
  `attr_principal`, `end_user`) still visit the heap for those columns - deliberately, to keep the
  entry narrow. An index-only scan relies on the visibility map, which autovacuum maintains.
* **Row-size cap:** the key and `INCLUDE` columns form one btree tuple, subject to its ~2.7 kB cap.
  Real values are UUIDs, slugs and short enums, far below it - which matters because the table's
  design forbids anything that could reject the uploader's insert.
* **Write cost:** one more index entry per inserted row. `d8e4f1a2b6c3` estimated ~15-20% per
  insert for a bare `(start_time)` index; this entry is wider, so read that figure as a floor.
* **A plain `CREATE INDEX` on the partitioned parent.** PostgreSQL recurses it to every
  partition, and every future partition inherits it. It is deliberately neither `CONCURRENTLY`
  nor inside an `autocommit_block`:
  - `env.py` runs every migration in one transaction holding
    `LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE` to serialise pods; an autocommit block
    would commit that transaction and drop the lock.
  - PostgreSQL cannot build an index `CONCURRENTLY` on a partitioned parent.
  - It ships in the same release as the ledger table, so in production the table is empty when
    this runs. On an already-loaded ledger (dev, sandbox) the build blocks the uploader's
    writes - never reads - for its duration, measured at ~2 min for a 14.5M-row partition, and
    the uploader's next run simply waits.

For the uploader owner: the historical-load script that drops and recreates the ledger's
secondary indexes must include this index.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c6f15537a1b2"
down_revision: Union[str, None] = "f3e2d1c0b9a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "litellm_spend_logs"
INDEX = "ix_spend_logs_time_cover"


def upgrade() -> None:
    op.create_index(
        INDEX,
        TABLE,
        ["start_time"],
        postgresql_include=[
            "spend",
            "attr_category",
            "codemie_origin",
            "call_type",
            "codemie_workflow_id",
            "codemie_request_id",
            "attr_project",
            "attr_source",
        ],
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
