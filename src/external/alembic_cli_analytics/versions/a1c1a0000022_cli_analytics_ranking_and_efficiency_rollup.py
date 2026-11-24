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

"""CLI analytics: the ranking, Efficiency and Tools & Models rows of the start-day rollup (EPMCDME-17301).

Revision ID: a1c1a0000022
Revises: a1c1a0000019
Create Date: 2026-11-18

Five tables, one row set per (UTC start day of a session, subscription class, project-origin class), taken from the
live Repositories and Efficiency statements over that day's sessions, so a window of whole days is their sum:

- `user_day_ranking_rollup`: per (project, repository, branch) the sessions, turns, files changed, lines, tool calls and
  successes and the spend, with the raw names of the models without a price behind the spend (`unpriced`) and whether
  any usage of the group was priced (`priced`: the evidence that tells an unknown amount from a partial one, EPMCDME-17152).
  An empty name (no project, repository or branch) is stored as ''. The subscription and origin lists of a group are the
  classes of its rows.
- `user_day_ranking_money_rollup`: the money of the funding-route and personal-billed estimates per (project,
  repository), and the day's spend total with its unpriced raw model names (`kind` 'spend', 'route' or 'billed';
  `money_key` the route, or 'true' / 'false').
- `user_day_efficiency_rollup`: what the Overview rows do not hold: the cost of the dead sessions, the sessions per depth
  bucket (turns: 1, 2-5, 6-10, 11-25, 26-50, 50+), the files written and edited, the sessions that have a cost row, and
  the day's worst session by cache-read tokens per API call (its id and ratio; the window's worst is the largest ratio,
  ties to the lowest id). The rest of the Efficiency tab is read from the Overview rows.
- `user_day_tools_rollup`: the Tools & Models tab's counts per (`kind`, `name`): `kind` 'tool' with its calls and
  successes, 'skill', 'agent' and 'command' with their invocations (`successes` 0). Every name of the day is stored; the
  window's top lists are cut after its days are summed.
- `user_day_tools_models`: per normalised model the sessions that used it and their tokens (input, output, cache read
  and cache creation).

No existing table changes; the days built before have a lower shape and are rebuilt by the job (`ROLLUP_SHAPE` 8).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000022"
down_revision: str | None = "a1c1a0000019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

CREATE_STATEMENTS = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    """CREATE TABLE IF NOT EXISTS user_day_ranking_rollup (
    start_day      date    NOT NULL,
    sub_class      text    NOT NULL,
    origin_class   text    NOT NULL,
    project        text    NOT NULL,
    repository     text    NOT NULL,
    branch         text    NOT NULL,
    session_count  bigint  NOT NULL,
    turns          bigint  NOT NULL,
    files_changed  bigint  NOT NULL,
    lines_added    bigint,
    lines_removed  bigint,
    tool_calls     bigint  NOT NULL,
    tool_successes bigint  NOT NULL,
    spend          numeric NOT NULL,
    unpriced       text[]  NOT NULL DEFAULT '{}',
    priced         boolean NOT NULL DEFAULT false,
    PRIMARY KEY (start_day, sub_class, origin_class, project, repository, branch)
)""",
    """CREATE TABLE IF NOT EXISTS user_day_ranking_money_rollup (
    start_day    date    NOT NULL,
    sub_class    text    NOT NULL,
    origin_class text    NOT NULL,
    kind         text    NOT NULL,
    money_key    text    NOT NULL,
    project      text    NOT NULL,
    repository   text    NOT NULL,
    cost         numeric NOT NULL,
    sessions     bigint  NOT NULL,
    unpriced     text[]  NOT NULL DEFAULT '{}',
    PRIMARY KEY (start_day, sub_class, origin_class, kind, money_key, project, repository)
)""",
    """CREATE TABLE IF NOT EXISTS user_day_efficiency_rollup (
    start_day         date             NOT NULL,
    sub_class         text             NOT NULL,
    origin_class      text             NOT NULL,
    dead_cost         numeric          NOT NULL,
    depth_1           bigint           NOT NULL,
    depth_2_5         bigint           NOT NULL,
    depth_6_10        bigint           NOT NULL,
    depth_11_25       bigint           NOT NULL,
    depth_26_50       bigint           NOT NULL,
    depth_50_plus     bigint           NOT NULL,
    files_written     bigint           NOT NULL,
    files_edited      bigint           NOT NULL,
    spend_sessions    bigint           NOT NULL,
    worst_session_id  text,
    worst_ratio       double precision,
    PRIMARY KEY (start_day, sub_class, origin_class)
)""",
    """CREATE TABLE IF NOT EXISTS user_day_tools_rollup (
    start_day    date   NOT NULL,
    sub_class    text   NOT NULL,
    origin_class text   NOT NULL,
    kind         text   NOT NULL,
    name         text   NOT NULL,
    calls        bigint NOT NULL,
    successes    bigint NOT NULL,
    PRIMARY KEY (start_day, sub_class, origin_class, kind, name)
)""",
    """CREATE TABLE IF NOT EXISTS user_day_tools_models (
    start_day    date   NOT NULL,
    sub_class    text   NOT NULL,
    origin_class text   NOT NULL,
    model        text   NOT NULL,
    sessions     bigint NOT NULL,
    total_tokens bigint NOT NULL,
    PRIMARY KEY (start_day, sub_class, origin_class, model)
)""",
]

DOWNGRADE_STATEMENTS = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    "DROP TABLE IF EXISTS user_day_tools_models",
    "DROP TABLE IF EXISTS user_day_tools_rollup",
    "DROP TABLE IF EXISTS user_day_efficiency_rollup",
    "DROP TABLE IF EXISTS user_day_ranking_money_rollup",
    "DROP TABLE IF EXISTS user_day_ranking_rollup",
]


def upgrade() -> None:
    for statement in CREATE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
