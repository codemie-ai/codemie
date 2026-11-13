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

"""relax_model_whitelist_default_check

Revision ID: c8d2f6a1b9e3
Revises: aad1456a4f17
Create Date: 2026-11-12 00:00:00.000000

EPMCDME-17119: allow a project to store allowed_models NULL (all models, including future ones) together with a
default_model. A stored list still requires a default_model.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c8d2f6a1b9e3"
down_revision: Union[str, None] = "aad1456a4f17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_model_whitelist_requires_default", "applications", schema="codemie", type_="check")
    op.create_check_constraint(
        "ck_model_whitelist_requires_default",
        "applications",
        "allowed_models IS NULL OR default_model IS NOT NULL",
        schema="codemie",
    )


def downgrade() -> None:
    # The strict check cannot be restored without changing rows saved as NULL allowed_models with a default_model;
    # code from before this change already reads that state, so the relaxed check stays in place.
    pass
