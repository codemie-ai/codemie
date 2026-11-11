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

"""delete_orphan_user_projects

One-time cleanup: deletes user_projects rows whose project_name matches no applications row
by id or name, compared case-insensitively. Rows matching an application exactly, only by
casing, or matching a soft-deleted application are kept. Every deleted row is logged.
The deletion is irreversible; downgrade only logs.

Revision ID: f7c3a9d1e4b6
Revises: f8d24a6c0b17
Create Date: 2026-10-16 00:00:00.000000

"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7c3a9d1e4b6"
down_revision: Union[str, None] = "f8d24a6c0b17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)

# `name IS NOT NULL` matters: `x IN (set holding NULL)` yields NULL instead of false.
# Matching is equality on LOWER(...), never LIKE, so `_` and `%` in names stay literal.
# deleted_at is deliberately not filtered: members of a soft-deleted application are kept.
_SELECT_ROWS = sa.text(
    "SELECT up.id, up.user_id, up.project_name, up.is_default, "
    "up.project_name IN (SELECT id FROM codemie.applications "
    "UNION SELECT name FROM codemie.applications WHERE name IS NOT NULL) AS exact_match, "
    "LOWER(up.project_name) IN (SELECT LOWER(id) FROM codemie.applications "
    "UNION SELECT LOWER(name) FROM codemie.applications WHERE name IS NOT NULL) AS any_case_match "
    "FROM codemie.user_projects up ORDER BY up.project_name, up.user_id"
)
# Re-checks the no-match condition so an application created after the SELECT keeps its members.
_DELETE_ORPHANS = sa.text(
    "DELETE FROM codemie.user_projects up WHERE up.id = ANY(:ids) "
    "AND LOWER(up.project_name) NOT IN (SELECT LOWER(id) FROM codemie.applications "
    "UNION SELECT LOWER(name) FROM codemie.applications WHERE name IS NOT NULL)"
)


def _classify(exact_match: bool, any_case_match: bool) -> str:
    """Return "exact", "case_only" or "orphan" for a user_projects row's match flags."""
    if exact_match:
        return "exact"
    if any_case_match:
        return "case_only"
    return "orphan"


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(_SELECT_ROWS).fetchall()

    exact_kept = 0
    case_only_kept = 0
    orphan_ids: list[str] = []
    for row_id, user_id, project_name, is_default, exact_match, any_case_match in rows:
        row_class = _classify(exact_match, any_case_match)
        if row_class == "exact":
            exact_kept += 1
        elif row_class == "case_only":
            case_only_kept += 1
        else:
            logger.info(
                "Deleting orphan user_projects row: user_id=%r project_name=%r is_default=%r",
                user_id,
                project_name,
                is_default,
            )
            orphan_ids.append(row_id)

    orphans_deleted = 0
    if orphan_ids:
        orphans_deleted = bind.execute(_DELETE_ORPHANS, {"ids": orphan_ids}).rowcount

    logger.info(
        "Orphan user_projects cleanup: scanned=%d exact_kept=%d case_only_kept=%d orphans_deleted=%d",
        len(rows),
        exact_kept,
        case_only_kept,
        orphans_deleted,
    )


def downgrade() -> None:
    # Deleted orphan rows referenced no project and cannot be restored; nothing to revert.
    logger.info("Orphan user_projects cleanup is intentionally not reverted; no data changed.")
