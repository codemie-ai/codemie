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

"""scope_ms_teams_project_singleton_to_project_type

Revision ID: a8032409ff23
Revises: 9c1d2e3f4a5b
Create Date: 2026-09-08 00:00:01.000000

uix_settings_project_ms_teams_singleton enforced uniqueness on project_name for every
credential_type='MS_TEAMS' row regardless of setting_type. Since USER-scope ms_teams
settings now also carry project_name (mirroring every other USER-scope credential type),
that index fired a false "already exists" IntegrityError whenever a user tried to create
a personal ms_teams integration for a project that already had a PROJECT-scope one.
Narrow the index to setting_type='PROJECT' so it only guards the per-project singleton
it was meant for.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8032409ff23"
down_revision: Union[str, None] = "9c1d2e3f4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Recreate the ms_teams project singleton index scoped to setting_type='PROJECT'.

    Uses IF EXISTS/IF NOT EXISTS because f1a2b3c4d5e6, which originally created this
    index, sits on a separate alembic branch that some deployments reach without ever
    running it — leaving nothing here for a plain DROP INDEX to find.
    """
    op.execute(sa.text("DROP INDEX IF EXISTS uix_settings_project_ms_teams_singleton"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uix_settings_project_ms_teams_singleton "
            "ON settings (project_name) "
            "WHERE (credential_type = 'MS_TEAMS' AND setting_type = 'PROJECT')"
        )
    )


def downgrade() -> None:
    """Restore the original, unscoped ms_teams project singleton index."""
    op.execute(sa.text("DROP INDEX IF EXISTS uix_settings_project_ms_teams_singleton"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uix_settings_project_ms_teams_singleton "
            "ON settings (project_name) "
            "WHERE (credential_type = 'MS_TEAMS')"
        )
    )
