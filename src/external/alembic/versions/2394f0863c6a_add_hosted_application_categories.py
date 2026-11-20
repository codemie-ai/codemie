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


"""Add hosted application categories: 0-3 shared-platform categories per hosted app, same shape as workflows.

Revision ID: 2394f0863c6a
Revises: 64825fe31931

``hosted_applications.categories`` is a JSONB array of shared category ids (``codemie.rest_api.models.category``),
with the same default and GIN index shape the workflows table uses for its own ``categories`` column.
"""

from alembic import op

revision = "2394f0863c6a"
down_revision = "64825fe31931"
branch_labels = None
depends_on = None

CATEGORIES_INDEX = "ix_hosted_applications_categories"
# Adding a NOT NULL column with a constant default is a fast, same-transaction DDL change; a short lock
# timeout fails and retries a migration pod instead of queueing every writer of hosted_applications behind it.
_LOCK_TIMEOUT = "5s"


def upgrade():
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.execute("ALTER TABLE hosted_applications ADD COLUMN categories JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute(f"CREATE INDEX {CATEGORIES_INDEX} ON hosted_applications USING gin (categories)")


def downgrade():
    op.execute(f"DROP INDEX {CATEGORIES_INDEX}")
    op.execute("ALTER TABLE hosted_applications DROP COLUMN categories")
