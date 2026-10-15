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

"""migrate_assistant_project_mapping_to_settings

Revision ID: c3d4e5f6a7b8
Revises: 8b2c1a4d5e6f
Create Date: 2026-08-27 00:00:00.000000

"""

import logging
from datetime import datetime, timezone
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "8b2c1a4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)


def _group_assistant_ids(rows: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Group (project_name, assistant_id) rows into deduplicated per-project lists,
    preserving first-seen order.
    """
    by_project: dict[str, list[str]] = {}
    for project_name, assistant_id in rows:
        ids = by_project.setdefault(project_name, [])
        if assistant_id not in ids:
            ids.append(assistant_id)
    return by_project


def _merge_assistant_ids(existing_credential_values: list[dict], new_ids: list[str]) -> list[dict]:
    """Merge new_ids into the existing credential_values' assistant_ids entry (dedup,
    preserving the existing order and appending only ids not already present). If no
    assistant_ids entry exists yet, one is added.
    """
    merged = [dict(entry) for entry in existing_credential_values]
    for entry in merged:
        if entry.get("key") == "assistant_ids":
            current = list(entry.get("value") or [])
            for assistant_id in new_ids:
                if assistant_id not in current:
                    current.append(assistant_id)
            entry["value"] = current
            return merged

    merged.append({"key": "assistant_ids", "value": list(new_ids)})
    return merged


def _filter_owned_assistant_ids(
    assistant_ids: list[str],
    project_name: str,
    assistants: dict[str, tuple[str | None, bool | None]],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Keep only assistant ids that exist and belong to project_name or are global.

    assistants maps assistant id -> (project, is_global). Returns the kept ids, in order, and
    the dropped ids with their reason: "deleted" or "foreign".
    """
    kept: list[str] = []
    dropped: list[tuple[str, str]] = []
    for assistant_id in assistant_ids:
        if assistant_id not in assistants:
            dropped.append((assistant_id, "deleted"))
            continue
        assistant_project, is_global = assistants[assistant_id]
        if assistant_project == project_name or is_global is True:
            kept.append(assistant_id)
        else:
            dropped.append((assistant_id, "foreign"))
    return kept, dropped


def _owned_assistant_ids(bind, project_name: str, assistant_ids: list[str]) -> list[str]:
    """Look the ids up in codemie.assistants, log each dropped id and return the kept ones."""
    rows = bind.execute(
        sa.text("SELECT id, project, is_global FROM codemie.assistants WHERE id = ANY(:ids)"),
        {"ids": assistant_ids},
    ).fetchall()
    assistants = {assistant_id: (project, is_global) for assistant_id, project, is_global in rows}
    kept, dropped = _filter_owned_assistant_ids(assistant_ids, project_name, assistants)
    for assistant_id, reason in dropped:
        logger.info(
            "Dropped assistant id %r from ms_teams migration for project %r: %s",
            assistant_id,
            project_name,
            reason,
        )
    return kept


def _build_alias(row_date: datetime | None, taken: set[str], now: datetime) -> str:
    """Build an ``msteams-<date>`` alias from row_date (or now when it is NULL), suffixed
    ``-2``, ``-3``, ... until it is not in taken.
    """
    base = f"msteams-{(row_date or now):%Y-%m-%d_%H-%M}"
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(
        sa.text("SELECT project_name, assistant_id FROM assistant_project_mapping " "ORDER BY project_name, created_at")
    ).fetchall()

    by_project = _group_assistant_ids(rows)

    insert_stmt = sa.text(
        "INSERT INTO codemie.settings "
        "(id, date, update_date, project_name, credential_type, setting_type, "
        "is_global, \"default\", user_id, alias, created_by, credential_values) "
        "VALUES "
        "(:id, :row_date, :row_date, :project_name, 'MS_TEAMS', 'PROJECT', "
        "false, false, NULL, :alias, NULL, :credential_values)"
    ).bindparams(sa.bindparam("credential_values", type_=JSONB))

    update_stmt = sa.text(
        "UPDATE codemie.settings SET credential_values = :credential_values, update_date = :row_date " "WHERE id = :id"
    ).bindparams(sa.bindparam("credential_values", type_=JSONB))

    for project_name, mapped_ids in by_project.items():
        assistant_ids = _owned_assistant_ids(bind, project_name, mapped_ids)
        existing = bind.execute(
            sa.text(
                "SELECT id, credential_values FROM codemie.settings "
                "WHERE project_name = :project_name AND credential_type = 'MS_TEAMS' LIMIT 1"
            ),
            {"project_name": project_name},
        ).fetchone()
        if existing is not None:
            existing_id, existing_credential_values = existing
            merged = _merge_assistant_ids(existing_credential_values or [], assistant_ids)
            logger.warning(
                "Merging ms_teams migration for project %r into existing settings row %r "
                "instead of inserting a new one; %d assistant_project_mapping row(s) merged.",
                project_name,
                existing_id,
                len(assistant_ids),
            )
            bind.execute(
                update_stmt,
                {
                    "id": existing_id,
                    "row_date": datetime.now(timezone.utc),
                    "credential_values": merged,
                },
            )
            continue

        taken = {
            alias
            for (alias,) in bind.execute(
                sa.text("SELECT alias FROM codemie.settings WHERE project_name = :project_name AND alias IS NOT NULL"),
                {"project_name": project_name},
            ).fetchall()
        }
        now = datetime.now(timezone.utc)
        bind.execute(
            insert_stmt,
            {
                "id": str(uuid4()),
                "row_date": now,
                "project_name": project_name,
                "alias": _build_alias(None, taken, now),
                "credential_values": [{"key": "assistant_ids", "value": assistant_ids}],
            },
        )

    op.drop_table("assistant_project_mapping")


def downgrade() -> None:
    op.create_table(
        "assistant_project_mapping",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("assistant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("feature", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["assistant_id"], ["assistants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assistant_id", "project_name", "feature", name="uix_assistant_project_mapping"),
    )
    op.create_index(
        op.f("ix_assistant_project_mapping_project_name"),
        "assistant_project_mapping",
        ["project_name"],
        unique=False,
    )
    # This downgrade is intentionally partial and non-destructive: it recreates the
    # empty assistant_project_mapping table but does not (and cannot) reconstruct its
    # rows from the merged ms_teams settings data, and it does not delete any
    # codemie.settings rows. Removing the MS_TEAMS credential_type value itself is
    # handled by revision 8b2c1a4d5e6f's own downgrade, one step further back; deleting
    # settings rows here would destroy data — including rows created after this
    # revision ran — with no way to distinguish migrated from newly-created rows and no
    # backup, so it is deliberately not done.
