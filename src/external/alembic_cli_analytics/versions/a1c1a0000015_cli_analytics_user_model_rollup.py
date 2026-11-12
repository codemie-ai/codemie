# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""CLI analytics: the per-model rows of the start-day rollup, for /cost (EPMCDME-17051).

Revision ID: a1c1a0000015
Revises: a1c1a0000014
Create Date: 2026-11-12

- `user_day_model_rollup`: one row per (UTC start day of a session, subscription class, project-origin class,
  normalised model) with the spend money of the sessions that used the model: the priced cost, the number of
  sessions and the unpriced raw model names. It is what /cost's "Cost by Model" sums over a window, next to the
  per-developer rows of `user_day_money_rollup`, so that /cost needs neither the session selection nor a pricing pass
  of the window.
- `user_rollup_day_state.shape`: which rows a built day has. A day built before this revision has shape 1 (no model
  rows) and is rebuilt by the job; the readers only trust the current shape.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000015"
down_revision: str | None = "a1c1a0000014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

CREATE_STATEMENTS = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    """CREATE TABLE IF NOT EXISTS user_day_model_rollup (
    start_day     date    NOT NULL,
    sub_class     text    NOT NULL,
    origin_class  text    NOT NULL,
    model         text    NOT NULL,
    cost          numeric NOT NULL,
    sessions      bigint  NOT NULL,
    unpriced      text[]  NOT NULL DEFAULT '{}',
    PRIMARY KEY (start_day, sub_class, origin_class, model)
)""",
    "ALTER TABLE user_rollup_day_state ADD COLUMN IF NOT EXISTS shape integer NOT NULL DEFAULT 1",
]

DOWNGRADE_STATEMENTS = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    "ALTER TABLE user_rollup_day_state DROP COLUMN IF EXISTS shape",
    "DROP TABLE IF EXISTS user_day_model_rollup",
]


def upgrade() -> None:
    for statement in CREATE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
