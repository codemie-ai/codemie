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

"""CLI analytics: the Overview rows of the start-day rollup (EPMCDME-17051).

Revision ID: a1c1a0000017
Revises: a1c1a0000016
Create Date: 2026-11-12

Three tables, one row set per (UTC start day of a session, subscription class, project-origin class), taken from the
live /overview statements over that day's sessions, so a window of whole days is their sum:

- `user_day_overview_rollup`: the totals of the Overview's cards (turns, tool calls and successes, duration, files
  changed, dead sessions, the spend and cache-read cost with the spend's unpriced raw names, the token and API-call
  sums).
- `user_day_overview_lines`: the lines added and removed per day the lines were written (`line_day`), and whether a row
  of that day reports lines: the "Net Lines Over Time" chart and `data_as_of`.
- `user_day_overview_models`: per normalised model, the sessions that used it and their spend from `cost_daily` (the
  tie-break of the model order): the "Sessions by Model" chart.

No existing table changes; the days built before have shape 2 and are rebuilt by the job (`ROLLUP_SHAPE` 3).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000017"
down_revision: str | None = "a1c1a0000016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

CREATE_STATEMENTS = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    """CREATE TABLE IF NOT EXISTS user_day_overview_rollup (
    start_day             date    NOT NULL,
    sub_class             text    NOT NULL,
    origin_class          text    NOT NULL,
    turns                 bigint  NOT NULL,
    tool_calls            bigint  NOT NULL,
    tool_successes        bigint  NOT NULL,
    duration_ms           bigint  NOT NULL,
    files_changed         bigint  NOT NULL,
    dead_sessions         bigint  NOT NULL,
    spend_cost            numeric NOT NULL,
    cache_read_cost       numeric NOT NULL,
    unpriced              text[]  NOT NULL DEFAULT '{}',
    input_tokens          bigint  NOT NULL,
    output_tokens         bigint  NOT NULL,
    cache_read_tokens     bigint  NOT NULL,
    cache_creation_tokens bigint  NOT NULL,
    api_calls             bigint  NOT NULL,
    PRIMARY KEY (start_day, sub_class, origin_class)
)""",
    """CREATE TABLE IF NOT EXISTS user_day_overview_lines (
    start_day     date    NOT NULL,
    sub_class     text    NOT NULL,
    origin_class  text    NOT NULL,
    line_day      date    NOT NULL,
    lines_added   bigint,
    lines_removed bigint,
    nonzero       boolean NOT NULL,
    PRIMARY KEY (start_day, sub_class, origin_class, line_day)
)""",
    """CREATE TABLE IF NOT EXISTS user_day_overview_models (
    start_day     date    NOT NULL,
    sub_class     text    NOT NULL,
    origin_class  text    NOT NULL,
    model         text    NOT NULL,
    sessions      bigint  NOT NULL,
    spend         numeric,
    PRIMARY KEY (start_day, sub_class, origin_class, model)
)""",
]

DOWNGRADE_STATEMENTS = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    "DROP TABLE IF EXISTS user_day_overview_models",
    "DROP TABLE IF EXISTS user_day_overview_lines",
    "DROP TABLE IF EXISTS user_day_overview_rollup",
]


def upgrade() -> None:
    for statement in CREATE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
