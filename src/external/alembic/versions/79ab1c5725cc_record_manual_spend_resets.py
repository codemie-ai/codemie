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

"""record_manual_spend_resets

Revision ID: 79ab1c5725cc
Revises: 633929ee02fa
Create Date: 2026-11-02 00:00:00.000000

EPMCDME-13429: the time of the last successful manual spend reset.

`spend_reset_at` records when an administrator last reset spend successfully by hand: for a project
budget on the budget row, for a personal budget per user and category on the assignment row. It is NULL
until the first reset. The provider's counter restarts at a reset, so spend screens count only from
this time.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "79ab1c5725cc"
down_revision: Union[str, None] = "633929ee02fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("budgets", sa.Column("spend_reset_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("user_budget_assignments", sa.Column("spend_reset_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("user_budget_assignments", "spend_reset_at")
    op.drop_column("budgets", "spend_reset_at")
