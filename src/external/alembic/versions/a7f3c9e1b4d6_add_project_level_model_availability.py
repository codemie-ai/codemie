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

"""add_project_level_model_availability

Revision ID: a7f3c9e1b4d6
Revises: d9e8f7a6b5c4
Create Date: 2026-10-02 00:00:00.000000

Project-Level Model Availability (EPMCDME-14349): admin-configurable allowed/default
LLM models per project.

Squashes what were previously three separate migrations (add_allowed_models,
add_default_model_to_applications, add_model_whitelist_default_constraint) into one.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7f3c9e1b4d6'
down_revision: Union[str, None] = 'd9e8f7a6b5c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add allowed_models column to applications table.
    op.add_column(
        'applications',
        sa.Column(
            'allowed_models',
            postgresql.ARRAY(sa.String()),
            nullable=True,
            server_default=None,
        ),
    )
    op.execute(
        "COMMENT ON COLUMN applications.allowed_models IS "
        "'List of allowed LLM model identifiers; NULL means all models allowed'"
    )

    # Add default_model column to applications table.
    op.add_column(
        'applications',
        sa.Column(
            'default_model',
            sa.String(),
            nullable=True,
            server_default=None,
        ),
    )
    op.execute(
        "COMMENT ON COLUMN applications.default_model IS "
        "'Admin-selected default LLM model for this project; used when model unavailable or not specified'"
    )

    # Add CHECK constraint to enforce that if allowed_models is set, default_model must also be set.
    op.create_check_constraint(
        'ck_model_whitelist_requires_default',
        'applications',
        '(allowed_models IS NULL AND default_model IS NULL) OR '
        '(allowed_models IS NOT NULL AND default_model IS NOT NULL)',
        schema='codemie',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'ck_model_whitelist_requires_default',
        'applications',
        schema='codemie',
        type_='check',
    )

    op.drop_column('applications', 'default_model')

    op.drop_column('applications', 'allowed_models')
