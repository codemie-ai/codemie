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

"""CLI Analytics: the session title column of session_dims.

Revision ID: a1c1a0000009
Revises: a1c1a0000006
Create Date: 2026-10-29

The down_revision must be repointed to the head on main if another CLI analytics migration lands first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000009"
down_revision: str | None = "a1c1a0000006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = ["ALTER TABLE session_dims ADD COLUMN session_title text"]

DOWNGRADE = ["ALTER TABLE session_dims DROP COLUMN session_title"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
