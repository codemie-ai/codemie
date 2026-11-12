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

"""CLI analytics: the per-user start-day rollup behind /users and /user-charts (EPMCDME-17051).

Revision ID: a1c1a0000014
Revises: a1c1a0000013
Create Date: 2026-11-11

Three new tables and nothing else, so no existing reader or writer is touched:

- `user_day_rollup`: one row per (UTC start day of a session, developer, subscription class, project-origin class) with
  what the Users page sums over the sessions of its window: session count, token sums, turns, tool calls and successes,
  net lines, the latest event and the keys of the developer's top model (so the top model of a window is the first of
  the rows' keys). The two classes are the values the `subscription_type` and `project_origin` filters compare, so a
  filtered request sums the rows of the classes it asks for.
- `user_day_money_rollup`: one row per (start day, developer, the two classes, funding route, personal-billed) with the
  priced money of the sessions, summed over every priced session and over those whose token rows all come from
  `cost_daily` (`*_cd`), with the unpriced raw model names of each.
- `user_rollup_day_state`: per start day, the signature of its sessions in `session_dims` and the rates version the
  money was priced with, which is how the job finds the days to rebuild and the reader the days it may trust.

A session sits in exactly one row, on the day it started, with its whole-lifetime values: a window is a plain sum, and
it equals the live statements for the sessions that started in the window.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000014"
down_revision: str | None = "a1c1a0000013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

CREATE_STATEMENTS = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    """CREATE TABLE IF NOT EXISTS user_day_rollup (
    start_day             date    NOT NULL,
    usr                   text    NOT NULL,
    sub_class             text    NOT NULL,
    origin_class          text    NOT NULL,
    session_count         bigint  NOT NULL,
    input_tokens          bigint  NOT NULL,
    output_tokens         bigint  NOT NULL,
    cache_read_tokens     bigint  NOT NULL,
    cache_creation_tokens bigint  NOT NULL,
    turns                 bigint  NOT NULL,
    tool_calls            bigint  NOT NULL,
    tool_successes        bigint  NOT NULL,
    net_lines             bigint,
    last_active           timestamptz,
    top_calls             bigint,
    top_day               date,
    top_session           text,
    top_email             text,
    top_model             text,
    top_qs                text,
    PRIMARY KEY (start_day, usr, sub_class, origin_class)
)""",
    """CREATE TABLE IF NOT EXISTS user_day_money_rollup (
    start_day       date    NOT NULL,
    usr             text    NOT NULL,
    sub_class       text    NOT NULL,
    origin_class    text    NOT NULL,
    funding_route   text    NOT NULL,
    personal_billed boolean NOT NULL,
    cost_all        numeric NOT NULL,
    sessions_all    bigint  NOT NULL,
    unpriced_all    text[]  NOT NULL DEFAULT '{}',
    cost_cd         numeric NOT NULL,
    sessions_cd     bigint  NOT NULL,
    unpriced_cd     text[]  NOT NULL DEFAULT '{}',
    PRIMARY KEY (start_day, usr, sub_class, origin_class, funding_route, personal_billed)
)""",
    """CREATE TABLE IF NOT EXISTS user_rollup_day_state (
    start_day     date             PRIMARY KEY,
    sessions      bigint           NOT NULL,
    active        bigint           NOT NULL,
    last_sum      numeric          NOT NULL,
    cost_sum      numeric          NOT NULL,
    ident_sum     numeric          NOT NULL,
    class_sum     numeric          NOT NULL,
    rates_version text             NOT NULL,
    built_at      timestamptz      NOT NULL DEFAULT now(),
    checked_at    timestamptz      NOT NULL DEFAULT now(),
    build_ms      integer          NOT NULL DEFAULT 0
)""",
]

DOWNGRADE_STATEMENTS = [
    "DROP TABLE IF EXISTS user_rollup_day_state",
    "DROP TABLE IF EXISTS user_day_money_rollup",
    "DROP TABLE IF EXISTS user_day_rollup",
]


def upgrade() -> None:
    for statement in CREATE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
