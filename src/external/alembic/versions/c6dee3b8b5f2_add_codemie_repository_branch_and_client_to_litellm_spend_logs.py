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

"""add codemie_repository, codemie_branch and codemie_client to litellm_spend_logs

Revision ID: c6dee3b8b5f2
Revises: ed3a7e5d3657
Create Date: 2026-11-13 00:00:00.000000

The location and client columns of the ledger contract: the git repository and branch a CLI request
was made in and the CLI client that made it, as the CLI reported them in `X-CodeMie-Repository`,
`X-CodeMie-Branch` and `X-CodeMie-Client` and the proxy stamped them into the spend-logs metadata.
They let spend be read per repository, branch and client, which no other ledger column can tell.

**Nullable, with no default and no CHECK**, for the reasons `codemie_origin` gives: most rows are
not CLI requests, older CLI versions send neither header, and an unexpected value is stored verbatim
rather than rejected at insert.

**No data migration.** No legacy row carries a repository; the metric store does, joinable on
`codemie_run_id`, and any backfill from it is a separate job.

**No index.** Branch names are high-cardinality free text, read only inside a time range already
pruned to its partitions. None of the three joins `litellm_spend_hourly`: branch names would
multiply its buckets, and nothing reads these columns by hour.

The `ALTER TABLE` runs on the **partitioned parent**: PostgreSQL propagates the columns to every
partition, the `DEFAULT` partition included, and with no default it is a catalog-only change.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6dee3b8b5f2"
down_revision: Union[str, None] = "ed3a7e5d3657"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "litellm_spend_logs"
COLUMNS = ("codemie_repository", "codemie_branch", "codemie_client")


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column(TABLE, sa.Column(column, sa.Text(), nullable=True))


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column(TABLE, column)
