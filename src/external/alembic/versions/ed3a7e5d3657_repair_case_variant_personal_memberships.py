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

"""repair_case_variant_personal_memberships

Intentionally a no-op (EPMCDME-16566). This revision first shipped as a one-time data repair of
user_projects rows that matched a personal project only by casing. The repair was dropped: the
application code resolves such users correctly without touching the rows, and the cleanup was
not required. The revision stays in the chain, with its id and parent unchanged, so databases
that already recorded it and later revisions that build on it keep upgrading and downgrading.

Revision ID: ed3a7e5d3657
Revises: c8d2f6a1b9e3
Create Date: 2026-11-12 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "ed3a7e5d3657"
down_revision: Union[str, None] = "c8d2f6a1b9e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No data is changed."""


def downgrade() -> None:
    """No data is changed."""
