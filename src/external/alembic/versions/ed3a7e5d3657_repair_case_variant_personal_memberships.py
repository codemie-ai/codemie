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

One-time repair (EPMCDME-16566): personal-project setup used to add a membership named after
the IdP email as delivered, so a user whose email casing differed from the stored application
name got a user_projects row matching their personal project only by casing (f7c3a9d1e4b6 keeps
such rows as "case_only"). For each such row of the user's own, live personal project:
- the real membership exists: the case-variant row is deleted, and when it was the user's
  default, the real row becomes the default;
- the real membership is missing: the row is renamed to the application's name, so the user
  keeps access to their project.
Rows of shared projects, of another user's project or of a soft-deleted project are kept.
Every changed row is logged, then one summary line with the counts. Downgrade only logs.

Revision ID: ed3a7e5d3657
Revises: c8d2f6a1b9e3
Create Date: 2026-11-12 00:00:00.000000

"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ed3a7e5d3657"
down_revision: Union[str, None] = "c8d2f6a1b9e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)

PERSONAL = "personal"

# Rows matching no application exactly (by id or name, as in f7c3a9d1e4b6) but one by LOWER(name);
# ix_applications_name_lower makes that application unique. Equality on LOWER(...), never LIKE.
_SELECT_CASE_VARIANTS = sa.text(
    "SELECT up.id, up.user_id, up.project_name, up.is_default, a.name, a.project_type, a.created_by, "
    "a.deleted_at IS NOT NULL AS app_deleted, "
    "EXISTS (SELECT 1 FROM codemie.user_projects r WHERE r.user_id = up.user_id AND r.project_name = a.name) "
    "AS has_real_row "
    "FROM codemie.user_projects up "
    "JOIN codemie.applications a ON LOWER(a.name) = LOWER(up.project_name) "
    "WHERE up.project_name NOT IN (SELECT id FROM codemie.applications "
    "UNION SELECT name FROM codemie.applications WHERE name IS NOT NULL) "
    "ORDER BY up.user_id, up.project_name"
)
# Each write re-checks its condition, so a membership created after the SELECT is never lost.
_DELETE_ORPHAN = sa.text(
    "DELETE FROM codemie.user_projects up WHERE up.id = :id "
    "AND EXISTS (SELECT 1 FROM codemie.user_projects r WHERE r.user_id = up.user_id AND r.project_name = :app_name)"
)
_RENAME_ORPHAN = sa.text(
    "UPDATE codemie.user_projects up SET project_name = :app_name WHERE up.id = :id "
    "AND NOT EXISTS (SELECT 1 FROM codemie.user_projects r WHERE r.user_id = up.user_id AND r.project_name = :app_name)"
)
# uix_user_projects_one_default allows one default per user: set it only when none is left.
_MOVE_DEFAULT = sa.text(
    "UPDATE codemie.user_projects SET is_default = true "
    "WHERE user_id = :user_id AND project_name = :app_name "
    "AND NOT EXISTS (SELECT 1 FROM codemie.user_projects d WHERE d.user_id = :user_id AND d.is_default)"
)


def _classify(user_id: str, project_type: str, created_by: str | None, app_deleted: bool, has_real_row: bool) -> str:
    """Return "delete", "rename", "not_personal", "other_owner" or "deleted_project" for a case-variant row."""
    if app_deleted:
        return "deleted_project"
    if project_type != PERSONAL:
        return "not_personal"
    if created_by != user_id:
        return "other_owner"
    return "delete" if has_real_row else "rename"


def _delete(bind, row_id: str, user_id: str, project_name: str, app_name: str, is_default: bool) -> tuple[int, int]:
    """Delete the case-variant row; move the default to the real row if it held it. Returns (deleted, moved)."""
    if not bind.execute(_DELETE_ORPHAN, {"id": row_id, "app_name": app_name}).rowcount:
        return 0, 0
    logger.info(
        "Deleted case-variant membership: user_id=%r project_name=%r real_project=%r is_default=%r",
        user_id,
        project_name,
        app_name,
        is_default,
    )
    if not is_default:
        return 1, 0
    return 1, bind.execute(_MOVE_DEFAULT, {"user_id": user_id, "app_name": app_name}).rowcount


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(_SELECT_CASE_VARIANTS).fetchall()

    counts = dict.fromkeys(
        ("deleted", "renamed", "defaults_moved", "not_personal", "other_owner", "deleted_project"), 0
    )
    for row_id, user_id, project_name, is_default, app_name, project_type, created_by, app_deleted, has_real in rows:
        action = _classify(user_id, project_type, created_by, app_deleted, has_real)
        if action == "rename":
            if bind.execute(_RENAME_ORPHAN, {"id": row_id, "app_name": app_name}).rowcount:
                logger.info(
                    "Renamed case-variant membership: user_id=%r project_name=%r -> %r",
                    user_id,
                    project_name,
                    app_name,
                )
                counts["renamed"] += 1
                continue
            action = "delete"  # the real membership appeared after the SELECT
        if action == "delete":
            deleted, moved = _delete(bind, row_id, user_id, project_name, app_name, is_default)
            counts["deleted"] += deleted
            counts["defaults_moved"] += moved
        else:
            counts[action] += 1

    logger.info(
        "Case-variant personal membership repair: scanned=%d deleted=%d renamed=%d defaults_moved=%d "
        "kept_not_personal=%d kept_other_owner=%d kept_deleted_project=%d",
        len(rows),
        counts["deleted"],
        counts["renamed"],
        counts["defaults_moved"],
        counts["not_personal"],
        counts["other_owner"],
        counts["deleted_project"],
    )


def downgrade() -> None:
    # The deleted rows duplicated a real membership and the renamed ones now carry the project's
    # real name; restoring the wrong casing would re-break the users, so nothing is reverted.
    logger.info("Case-variant personal membership repair is intentionally not reverted; no data changed.")
