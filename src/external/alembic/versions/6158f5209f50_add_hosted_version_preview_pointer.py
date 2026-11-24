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

"""Add the automatic cover pointer to hosted application versions.

Revision ID: 6158f5209f50
Revises: d5241114c3dd

The validator captures one picture of the start page of an accepted version. The picture is stored at
<attempt prefix>/preview.jpg. These nullable columns point at it. The content manifest does not hold the pointer.
All three columns are set together or all three stay empty. No version has a picture before this revision.
"""

from alembic import op

revision = "6158f5209f50"
down_revision = "d5241114c3dd"
branch_labels = None
depends_on = None

STATEMENTS = (
    "ALTER TABLE hosted_application_versions ADD COLUMN preview_key text",
    "ALTER TABLE hosted_application_versions ADD COLUMN preview_size integer",
    "ALTER TABLE hosted_application_versions ADD COLUMN preview_sha256 bytea",
    "ALTER TABLE hosted_application_versions ADD CONSTRAINT hosted_application_versions_preview_all_or_none "
    "CHECK ((preview_key IS NULL) = (preview_size IS NULL) AND (preview_key IS NULL) = (preview_sha256 IS NULL))",
    "ALTER TABLE hosted_application_versions ADD CONSTRAINT hosted_application_versions_preview_sha256_length "
    "CHECK (octet_length(preview_sha256) = 32)",
    "ALTER TABLE hosted_application_versions ADD CONSTRAINT hosted_application_versions_preview_size_range "
    "CHECK (preview_size BETWEEN 1 AND 1048576)",
)


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    # Dropping the columns drops the check constraints that name them.
    for column in ("preview_sha256", "preview_size", "preview_key"):
        op.execute(f"ALTER TABLE hosted_application_versions DROP COLUMN {column}")
