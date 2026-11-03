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

"""CLI Analytics session routing: classification evidence on hook rows, the durable session binding.

Revision ID: a1c1a0000007
Revises: a1c1a0000009
Create Date: 2026-10-28

Nullable columns only; rows written before this revision read as unknown classification and empty origin.
`session_dims.owner_email` set means the session is bound (EPMCDME-16440).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000007"
down_revision: str | None = "a1c1a0000009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = {
    "hook_events": [
        "subscription_type",
        "project_origin",
        "route_note",
        "client_subscription_type",
        "client_policy_version",
    ],
    "session_dims": ["owner_email", "subscription_type", "project_origin", "route_note"],
}

UPGRADE = [
    f"ALTER TABLE {table}\n" + ",\n".join(f"    ADD COLUMN {name} text" for name in names)
    for table, names in _COLUMNS.items()
]

DOWNGRADE = [
    f"ALTER TABLE {table}\n" + ",\n".join(f"    DROP COLUMN {name}" for name in names)
    for table, names in _COLUMNS.items()
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
