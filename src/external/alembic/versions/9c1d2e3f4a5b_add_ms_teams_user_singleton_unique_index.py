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

"""add_ms_teams_user_singleton_unique_index

Revision ID: 9c1d2e3f4a5b
Revises: fa14587c0de1
Create Date: 2026-09-08 00:00:00.000000

Backs the application-level check_ms_teams_exist_for_user read-then-write check with a
DB-level partial unique index, so two concurrent create requests for the same user
cannot both insert a USER-scoped ms_teams settings row.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c1d2e3f4a5b"
down_revision: Union[str, None] = "fa14587c0de1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add a partial unique index enforcing at most one USER-scope ms_teams row per user."""
    op.create_index(
        "uix_settings_user_ms_teams_singleton",
        "settings",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("credential_type = 'MS_TEAMS' AND setting_type = 'USER'"),
    )


def downgrade() -> None:
    """Drop the USER-scope ms_teams singleton partial unique index."""
    op.drop_index(
        "uix_settings_user_ms_teams_singleton",
        table_name="settings",
    )
