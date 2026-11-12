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

"""CLI analytics: count the recomputes of a start day's sessions, so the user rollup rebuilds it (EPMCDME-17051).

Revision ID: a1c1a0000016
Revises: a1c1a0000015
Create Date: 2026-11-12

- `user_rollup_day_state.touched`: how many times the rollup refresher recomputed a session that started on the day.
- `user_rollup_day_state.built_touched`: the value of `touched` the day's last build read before its data.

A day whose two values differ is rebuilt by the job. The day's signature reads `session_dims` only, so it misses facts
that leave the session row as it was (tool spans, line counts or unpriced tokens that arrive after the session's last
hook event); the counter does not. Both columns are added with a constant default: no table rewrite.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000016"
down_revision: str | None = "a1c1a0000015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

CREATE_STATEMENTS = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    "ALTER TABLE user_rollup_day_state ADD COLUMN IF NOT EXISTS touched bigint NOT NULL DEFAULT 0",
    "ALTER TABLE user_rollup_day_state ADD COLUMN IF NOT EXISTS built_touched bigint NOT NULL DEFAULT 0",
]

DOWNGRADE_STATEMENTS = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    "ALTER TABLE user_rollup_day_state DROP COLUMN IF EXISTS built_touched",
    "ALTER TABLE user_rollup_day_state DROP COLUMN IF EXISTS touched",
]


def upgrade() -> None:
    for statement in CREATE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
