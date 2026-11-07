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

"""CLI Analytics publication contract: the contract version, work consent and funding route of a session binding.

Revision ID: a1c1a0000010
Revises: a1c1a0000007
Create Date: 2026-11-04

Nullable columns only; NULL = a binding made before the contract (legacy).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000010"
down_revision: str | None = "a1c1a0000007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("contract_version", "work_consent", "funding_route")

UPGRADE = ["ALTER TABLE session_dims\n" + ",\n".join(f"    ADD COLUMN {name} text" for name in _COLUMNS)]

DOWNGRADE = ["ALTER TABLE session_dims\n" + ",\n".join(f"    DROP COLUMN {name}" for name in _COLUMNS)]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
