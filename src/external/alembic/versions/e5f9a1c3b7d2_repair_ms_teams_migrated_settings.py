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

"""repair_ms_teams_migrated_settings

Repairs MS_TEAMS settings rows written by c3d4e5f6a7b8 before it was corrected: decodes
credential_values stored as a jsonb string back into an array (dropping assistant ids that
are deleted, foreign to the project or malformed) and backfills NULL or empty aliases.

Revision ID: e5f9a1c3b7d2
Revises: y1z2a3b4c5d6
Create Date: 2026-10-15 00:00:00.000000

"""

import json
import logging
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "e5f9a1c3b7d2"
down_revision: Union[str, None] = "y1z2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)

ASSISTANT_IDS_KEY = "assistant_ids"


def _decode_credential_values(raw: object) -> list[dict] | None:
    """Return credential_values as a list: a JSON string is decoded, a list passes through.
    Invalid JSON, anything that is not an array, or an array holding a non-object element
    yields None.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, list) or not all(isinstance(entry, dict) for entry in raw):
        return None
    return raw


def _filter_assistant_ids(
    credential_values: list[dict],
    project_name: str,
    assistants: dict[str, tuple[str | None, bool | None]],
) -> tuple[list[dict], list[tuple[object, str]]]:
    """Keep only assistant ids that exist and belong to project_name or are global.

    assistants maps assistant id -> (project, is_global). Returns the new credential_values
    (other entries untouched, an assistant_ids entry always present) and the dropped ids with
    their reason: "deleted", "foreign" or "invalid" (non-string or duplicate).
    """
    new_values = [dict(entry) for entry in credential_values]
    dropped: list[tuple[object, str]] = []
    entry = next((e for e in new_values if e.get("key") == ASSISTANT_IDS_KEY), None)
    if entry is None:
        new_values.append({"key": ASSISTANT_IDS_KEY, "value": []})
        return new_values, dropped

    raw_ids = entry.get("value")
    if not isinstance(raw_ids, list):
        if raw_ids is not None:
            dropped.append((raw_ids, "invalid"))
        entry["value"] = []
        return new_values, dropped

    kept: list[str] = []
    for assistant_id in raw_ids:
        if not isinstance(assistant_id, str) or assistant_id in kept:
            dropped.append((assistant_id, "invalid"))
        elif assistant_id not in assistants:
            dropped.append((assistant_id, "deleted"))
        else:
            assistant_project, is_global = assistants[assistant_id]
            if assistant_project == project_name or is_global is True:
                kept.append(assistant_id)
            else:
                dropped.append((assistant_id, "foreign"))
    entry["value"] = kept
    return new_values, dropped


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


_SELECT_TARGETS = sa.text(
    "SELECT id, project_name, setting_type, user_id, alias, date, credential_values, "
    "jsonb_typeof(credential_values) "
    "FROM codemie.settings "
    "WHERE credential_type = 'MS_TEAMS' "
    "AND (jsonb_typeof(credential_values) = 'string' OR alias IS NULL OR alias = '') "
    "ORDER BY date NULLS LAST, id"
)
_SELECT_ASSISTANTS = sa.text("SELECT id, project, is_global FROM codemie.assistants WHERE id = ANY(:ids)")
_SELECT_PROJECT_ALIASES = sa.text(
    "SELECT alias FROM codemie.settings WHERE project_name = :project_name AND alias IS NOT NULL"
)
_SELECT_USER_ALIASES = sa.text(
    "SELECT alias FROM codemie.settings WHERE project_name = :project_name "
    "AND user_id IS NOT DISTINCT FROM :user_id AND alias IS NOT NULL"
)
_UPDATE_CREDENTIAL_VALUES = sa.text(
    "UPDATE codemie.settings SET credential_values = :credential_values WHERE id = :id"
).bindparams(sa.bindparam("credential_values", type_=JSONB))
_UPDATE_ALIAS = sa.text("UPDATE codemie.settings SET alias = :alias WHERE id = :id")


def _repair_credential_values(bind, row_id: str, project_name: str, credential_values: list[dict]) -> int:
    """Filter the decoded assistant ids, write the array back and return the number of ids dropped."""
    candidate_ids = [
        assistant_id
        for entry in credential_values
        if entry.get("key") == ASSISTANT_IDS_KEY and isinstance(entry.get("value"), list)
        for assistant_id in entry["value"]
        if isinstance(assistant_id, str)
    ]
    assistants = {}
    if candidate_ids:
        rows = bind.execute(_SELECT_ASSISTANTS, {"ids": candidate_ids}).fetchall()
        assistants = {assistant_id: (project, is_global) for assistant_id, project, is_global in rows}

    new_values, dropped = _filter_assistant_ids(credential_values, project_name, assistants)
    for assistant_id, reason in dropped:
        logger.info(
            "Dropped assistant id %r from MS Teams settings row %r (project %r): %s",
            assistant_id,
            row_id,
            project_name,
            reason,
        )
    bind.execute(_UPDATE_CREDENTIAL_VALUES, {"id": row_id, "credential_values": new_values})
    return len(dropped)


def _taken_aliases(
    bind, project_name: str, setting_type: str, user_id: str | None, assigned: list[tuple[str, str | None, str]]
) -> set[str]:
    """Aliases already used in the row's check_alias_unique scope: in the DB or assigned earlier this run.

    PROJECT scope is the whole project; USER scope is the project plus user_id.
    """
    if setting_type == "USER":
        result = bind.execute(_SELECT_USER_ALIASES, {"project_name": project_name, "user_id": user_id})
        in_run = {alias for project, user, alias in assigned if project == project_name and user == user_id}
    else:
        result = bind.execute(_SELECT_PROJECT_ALIASES, {"project_name": project_name})
        in_run = {alias for project, _, alias in assigned if project == project_name}
    return {alias for (alias,) in result.fetchall()} | in_run


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    rows = bind.execute(_SELECT_TARGETS).fetchall()

    decoded = aliased = ids_dropped = skipped = 0
    assigned: list[tuple[str, str | None, str]] = []
    for row_id, project_name, setting_type, user_id, alias, row_date, raw_values, values_type in rows:
        if values_type == "string":
            credential_values = _decode_credential_values(raw_values)
            if credential_values is None:
                logger.warning(
                    "Skipping MS Teams settings row %r (project %r): credential_values is not a JSON array of objects",
                    row_id,
                    project_name,
                )
                skipped += 1
                continue
            ids_dropped += _repair_credential_values(bind, row_id, project_name, credential_values)
            decoded += 1

        if not alias:
            taken = _taken_aliases(bind, project_name, setting_type, user_id, assigned)
            new_alias = _build_alias(row_date, taken, now)
            bind.execute(_UPDATE_ALIAS, {"id": row_id, "alias": new_alias})
            assigned.append((project_name, user_id, new_alias))
            aliased += 1

    logger.info(
        "MS Teams settings repair: scanned=%d decoded=%d aliased=%d ids_dropped=%d skipped=%d",
        len(rows),
        decoded,
        aliased,
        ids_dropped,
        skipped,
    )


def downgrade() -> None:
    # Re-encoding credential_values as a jsonb string would re-break the rows, and the
    # dropped assistant ids and generated aliases cannot be told apart from later edits,
    # so the repair is intentionally not reverted (as in c3d4e5f6a7b8's downgrade).
    logger.info("MS Teams settings repair is intentionally not reverted; no data changed.")
