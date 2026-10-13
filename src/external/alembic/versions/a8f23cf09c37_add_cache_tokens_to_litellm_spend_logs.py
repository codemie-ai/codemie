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

"""add cache tokens to litellm_spend_logs

Revision ID: a8f23cf09c37
Revises: 5189d4d2afaf
Create Date: 2026-10-12 00:00:00.000000

Two columns beside `total_tokens`: `cache_read_input_tokens` (prompt tokens served from the provider's
prompt cache) and `cache_creation_input_tokens` (prompt tokens written to it). They are billed at rates
different from plain input, so without them the ledger cannot say what a request's prompt actually cost.
The uploader copies them from `LiteLLM_SpendLogs.metadata -> additional_usage_values`, where LiteLLM
already normalises every provider's shape into these two keys.

**Both are subsets of `prompt_tokens`, not additions to it.** LiteLLM reports `prompt_tokens` as the
OpenAI-style superset, so uncached input is `prompt_tokens - cache_read_input_tokens -
cache_creation_input_tokens`. Over a week of production rows the two never exceeded `prompt_tokens` for
any provider.

**Nullable, with no default and no CHECK.** NULL means the provider reported nothing (most azure, vertex
and bedrock_mantle rows); 0 means it reported no cache activity (every bedrock row does). A server
default would turn "unknown" into "zero". A CHECK would reject the uploader's insert over a value it
does not understand, and a row that never lands is a silent gap in a period the table is published as
covering - so a value that is not a plain non-negative integer is stored as NULL by the uploader and
surfaced as a counter instead.

**No data migration, no index.** Nothing in a legacy row derives these counts; existing rows stay NULL
until the uploader re-delivers their window, which `ON_CONFLICT=update` turns into a backfill. The columns
are only read inside aggregates over a range already pruned to a month's partitions, so an index would be
paid on every insert and used by nothing.

The `ALTER TABLE` runs on the **partitioned parent**. PostgreSQL propagates a parent's new column to every
partition, the `DEFAULT` partition included, and with no default value attached it is a catalog-only
change: no table is rewritten and no partition is scanned.

**Deploy this before the uploader release that writes the columns.** That uploader names both on every
write, so without them every page fails. The old uploader is unaffected by the new columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8f23cf09c37"
down_revision: Union[str, None] = "5189d4d2afaf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "litellm_spend_logs"
COLUMNS = ("cache_read_input_tokens", "cache_creation_input_tokens")


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column(TABLE, sa.Column(column, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    for column in COLUMNS:
        op.drop_column(TABLE, column)
