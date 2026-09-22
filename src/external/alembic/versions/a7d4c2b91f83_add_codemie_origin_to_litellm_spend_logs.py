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

"""add codemie_origin to litellm_spend_logs

Revision ID: a7d4c2b91f83
Revises: c9a1b2d3e4f5
Create Date: 2026-09-04 00:00:00.000000

The second dimension column of the published contract (v2.1), beside `codemie_workflow_id`: it says
**what a request was for** - a conversation, a workflow run, an indexing job, a CLI session - which is
the one thing the ledger's own columns cannot tell. `attr_category` answers *which budget paid*, and a
CLI session billed to a premium budget proves the two are different questions: before this column such
a row was invisible to every CLI report.

**Nullable, with no default and no CHECK.** An origin is stamped by the sender or it is unknown: a row
whose caller published none legitimately carries NULL, and so does every row written before the
sending release. A server default would assert an origin nobody claimed. A CHECK would reject the
uploader's insert over a vocabulary word it does not know - and a row that never lands above a
published coverage floor is a silent gap in a period the table is published as covering - so, as
everywhere else in this table, an unexpected value is stored verbatim and surfaced as a defect.

**No data migration, because nothing in a legacy row derives one.** The origin is what the caller was
doing, and that intent exists nowhere in the 28 columns already written; inferring it from
`attr_category` or `call_type` would put a guess in a column whose whole value is that it was
asserted. NULL is not a hole to be filled: the class predicates read it as *classify this row by the
native columns it already carries*, which is exactly how every one of these rows is classified today.

**No index.** The column is read inside `FILTER (WHERE ...)` aggregates and in a class-narrowed
`WHERE`, always over a range already pruned to a month's partitions and scanned in full there; an index on a low-cardinality word would be
paid on every insert and used by nothing.

The `ALTER TABLE` runs on the **partitioned parent**. PostgreSQL propagates a parent's new column to
every partition, the `DEFAULT` partition included, and with no default value attached it is a
catalog-only change: no table is rewritten and no partition is scanned.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d4c2b91f83"
down_revision: Union[str, None] = "c9a1b2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "litellm_spend_logs"
COLUMN = "codemie_origin"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
