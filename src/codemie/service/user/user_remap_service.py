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

"""Remaps a pre-Keycloak users.id to a Keycloak-issued id, in place (EPMCDME-16560).

A user who registered before Keycloak migration keeps the id their original IDP issued;
Keycloak later issues a different `sub` for the same person. UserRemapService.remap_user
rewrites that one row's id and every table that references the old id, in a single
transaction, so the user's history/assets move with them. Username, name and email on the
existing row are NOT changed: only the id moves.
This is a request-triggered, repeatable service, not an Alembic migration: `remap_user_flow`
owns the session and its commit (matches `UserManagementService.deactivate_user_flow`'s
convention), `remap_user` itself stays session-agnostic for callers that already hold one.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlmodel import Session, select

from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_repository import user_repository
from codemie.rest_api.models.user_management import CodeMieUserDetail, UserDB
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    UserManagementEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository

_USER_NOT_FOUND = "User not found"

# Every single-column FOREIGN KEY whose target is users.id (in the public or codemie schema),
# discovered at run time so that a new FK added by a future migration is repointed too.
# The FK check is deferred to COMMIT (migration c6f1a2b3d4e5), so a child row still holding the
# old id would fail the transaction at commit; this pass makes sure none is left behind.
_FK_COLUMNS_REFERENCING_USERS_ID_SQL = text(
    """
    SELECT c.conrelid::regclass::text AS table_name, a.attname AS column_name
    FROM pg_constraint c
    JOIN pg_class r ON r.oid = c.confrelid
    JOIN pg_namespace n ON n.oid = r.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
    WHERE c.contype = 'f'
      AND r.relname = 'users'
      AND n.nspname IN ('public', 'codemie')
      AND array_length(c.conkey, 1) = 1
      AND c.confkey[1] = (
          SELECT attnum FROM pg_attribute WHERE attrelid = r.oid AND attname = 'id'
      )
    """
)

# True-FK and 1:1-PK tables: a plain `user_id` column keyed to `users.id`.
_USER_ID_FK_TABLES = (
    "user_projects",
    "user_knowledge_bases",
    "email_verification_tokens",
    "user_preferences",
    "user_profile_settings",
)

# FKs into users are DEFERRABLE INITIALLY DEFERRED by Alembic migration c6f1a2b3d4e5, so the
# PK move on `users` is checked at COMMIT, not per statement. Without that migration an
# IMMEDIATE FK would reject `UPDATE users SET id=:new` while children still point at `:old`.

# (table, column) pairs for denormalized `user_id`/`created_by` columns with no DB-level FK.
#
# NOTE on scope: `cli_analytics.user_id` lives in a separately configured analytics Postgres
# (CLI_ANALYTICS_PG_URL may point at a different database entirely - see
# .ai-run/guides/integration/cli-analytics-storage.md) and cannot be rewritten inside this
# service's single `with get_session()` transaction, so it is intentionally excluded here.
#
# NOTE on scope: `agent_workspace_files.blob_owner` is intentionally excluded. It keys the
# physical blob location in FileRepository (e.g. `<base_dir>/<owner>/<name>` on the filesystem
# backend), and AgentWorkspaceService._upsert_workspace_file_reference deliberately keeps it
# pinned to the original uploader. Rewriting the column here without moving the underlying
# blob would leave file references pointing at a storage key that no longer exists.
_PLAIN_ID_COLUMNS = (
    ("a2a_tasks", "user_id"),
    ("agent_workspaces", "user_id"),
    ("applications", "created_by"),
    ("assistant_clone_event", "user_id"),
    ("assistant_prompt_variable_mapping", "user_id"),
    ("assistant_user_interaction", "user_id"),
    ("assistant_user_mapping", "user_id"),
    ("budgets", "created_by"),
    ("conversation_analytics", "user_id"),
    ("conversation_folders", "user_id"),
    ("conversations", "user_id"),
    ("conversation_metrics", "user_id"),
    ("cost_centers", "created_by"),
    ("kata_user_interaction", "user_id"),
    ("leaderboard_entries", "user_id"),
    ("mcp_configs", "user_id"),
    ("project_budget_groups", "created_by"),
    ("project_member_budget_assignments", "user_id"),
    ("project_spend_tracking", "user_id"),
    ("settings", "user_id"),
    ("shared_conversations", "shared_by_user_id"),
    ("skill_events", "user_id"),
    ("skill_user_interaction", "user_id"),
    ("user_data", "user_id"),
    ("user_kata_progress", "user_id"),
)

# (table, column) pairs holding a `CreatedByUser` JSONB blob (id+username+name embedded
# together). Only the `id` member changes; username and name are left as they are.
_CREATED_BY_JSONB_COLUMNS = (
    ("assistants", "created_by"),
    ("assistant_configurations", "created_by"),
    ("guardrails", "created_by"),
    ("guardrail_assignments", "created_by"),
    ("index_info", "created_by"),
    ("index_info", "updated_by"),
    ("mcp_configs", "created_by"),
    ("permissions", "created_by"),
    ("settings", "created_by"),
    ("skills", "created_by"),
)

# (table, column) pairs holding a `UserEntity` JSONB blob. Ownership is keyed by `user_id`, not `id`.
_USER_ENTITY_JSONB_COLUMNS = (
    ("workflows", "created_by"),
    ("workflow_executions", "created_by"),
)


class UserRemapService:
    """Rewrites every DB reference from an old users.id to a new one, in one transaction."""

    @staticmethod
    def remap_user_flow(old_user_id: str, new_user_id: str, actor_user_id: str) -> Optional[CodeMieUserDetail]:
        """Remap `old_user_id` to `new_user_id` and return the remapped user's detail view.

        Owns the session and its commit end to end (matches
        `UserManagementService.deactivate_user_flow`'s convention); the router only validates
        the feature flag and delegates here.
        """
        from codemie.clients.postgres import get_session
        from codemie.service.user.user_management_service import user_management_service

        with get_session() as session:
            remapped = UserRemapService.remap_user(session, old_user_id, new_user_id, actor_user_id)
            session.commit()
            return user_management_service.get_user_with_relationships(session, remapped.id, actor_user_id, True, False)

    @staticmethod
    def remap_user(
        session: Session,
        old_user_id: str,
        new_user_id: str,
        actor_user_id: str,
    ) -> UserDB:
        """Remap `old_user_id` to `new_user_id`, transferring the id and every reference to it.

        Only the id changes. Username, name and email on the row are left untouched.
        This is a PK change on the existing row, never a merge of two rows: `old_user_id`
        must already exist.

        Args:
            session: Caller-owned session; the caller commits (see `remap_user_flow`).
            old_user_id: The pre-Keycloak id currently on the row.
            new_user_id: The Keycloak-issued id to move the row to.
            actor_user_id: The superadmin performing the remap (for the audit log).

        Returns:
            The UserDB row at its new id.

        Raises:
            ExtendedHTTPException: 404 if `old_user_id` has no row; 409 if `new_user_id` is
                already the id of a different row.
        """
        if not UserRemapService._lock_old_user_row(session, old_user_id):
            already_remapped = session.get(UserDB, new_user_id)
            if already_remapped is not None:
                return already_remapped
            raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)
        UserRemapService._preflight_check_conflicts(session, old_user_id, new_user_id)

        UserRemapService._remap_core_row_and_true_fk_tables(session, old_user_id, new_user_id)
        UserRemapService._update_plain_id_columns(session, old_user_id, new_user_id)
        UserRemapService._update_fk_columns_referencing_users_id(session, old_user_id, new_user_id)
        UserRemapService._update_created_by_jsonb(session, old_user_id, new_user_id)

        activity_event_repository.insert(
            ActivityEventCreate(
                domain=ActivityDomain.USER_MANAGEMENT,
                event_type=UserManagementEvent.USER_REMAPPED,
                entity_type=ActivityEntityType.USER,
                entity_id=new_user_id,
                actor_id=actor_user_id,
                attributes={"old_id": old_user_id, "new_id": new_user_id, "actor_user_id": actor_user_id},
            ),
            session,
        )

        remapped = user_repository.get_by_id(session, new_user_id)
        if not remapped:
            raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)
        return remapped

    @staticmethod
    def _lock_old_user_row(session: Session, old_user_id: str) -> bool:
        """Lock the old `users` row (SELECT ... FOR UPDATE) for the rest of the transaction.

        Serialises concurrent remaps of the same user: a second caller blocks here until the
        first commits or rolls back, then sees the row gone and takes the already-remapped path.
        Returns False when no row has `old_user_id`.
        """
        return session.exec(select(UserDB).where(UserDB.id == old_user_id).with_for_update()).first() is not None

    @staticmethod
    def _preflight_check_conflicts(session: Session, old_user_id: str, new_user_id: str) -> None:
        """Raise 409 if `new_user_id` already belongs to a *different* users row.

        Checked before any mutation, so a rejected request changes nothing and a PK collision
        never reaches the database as an unhandled IntegrityError (500).
        """
        existing = user_repository.get_by_id(session, new_user_id)
        if existing and existing.id != old_user_id:
            raise ExtendedHTTPException(code=409, message="User ID already exists")

    @staticmethod
    def _remap_core_row_and_true_fk_tables(session: Session, old_user_id: str, new_user_id: str) -> None:
        """Move the `users` PK and cascade it through true-FK / 1:1-PK tables."""
        # No schema change here: the deferrable FKs come from the migration (see module note above).
        session.execute(text("SET CONSTRAINTS ALL DEFERRED"))

        session.execute(
            text("UPDATE users SET id = :new_id WHERE id = :old_id"),
            {"new_id": new_user_id, "old_id": old_user_id},
        )

        for table in _USER_ID_FK_TABLES:
            session.execute(
                text(f"UPDATE {table} SET user_id = :new_id WHERE user_id = :old_id"),  # noqa: S608
                {"new_id": new_user_id, "old_id": old_user_id},
            )

        # EPAM-sync table: schema-qualified. Its email is unchanged by a remap, so only user_id moves.
        session.execute(
            text("UPDATE codemie.user_enrichment SET user_id = :new_id WHERE user_id = :old_id"),
            {"new_id": new_user_id, "old_id": old_user_id},
        )

    @staticmethod
    def _update_plain_id_columns(session: Session, old_user_id: str, new_user_id: str) -> None:
        """Reassign denormalized `user_id`/`created_by`(str) columns with no DB-level FK."""
        for table, column in _PLAIN_ID_COLUMNS:
            session.execute(
                text(f"UPDATE {table} SET {column} = :new_id WHERE {column} = :old_id"),  # noqa: S608
                {"new_id": new_user_id, "old_id": old_user_id},
            )

    @staticmethod
    def _update_fk_columns_referencing_users_id(session: Session, old_user_id: str, new_user_id: str) -> None:
        """Repoint every FK column that references users.id (discovered from pg_constraint)."""
        for row in session.execute(_FK_COLUMNS_REFERENCING_USERS_ID_SQL).fetchall():
            column = '"' + row.column_name.replace('"', '""') + '"'
            session.execute(
                text(f"UPDATE {row.table_name} SET {column} = :new_id WHERE {column} = :old_id"),  # noqa: S608
                {"new_id": new_user_id, "old_id": old_user_id},
            )

    @staticmethod
    def _update_created_by_jsonb(session: Session, old_user_id: str, new_user_id: str) -> None:
        """Rewrite the `id` member of `CreatedByUser` JSONB blobs in place."""
        params = {"old_id": old_user_id, "new_id": new_user_id}
        for table, column in _CREATED_BY_JSONB_COLUMNS:
            session.execute(
                text(
                    f"UPDATE {table} "  # noqa: S608
                    f"SET {column} = jsonb_set({column}, '{{id}}', to_jsonb(CAST(:new_id AS text))) "
                    f"WHERE {column}->>'id' = :old_id"
                ),
                params,
            )

        for table, column in _USER_ENTITY_JSONB_COLUMNS:
            session.execute(
                text(
                    f"UPDATE {table} "  # noqa: S608
                    f"SET {column} = jsonb_set({column}, '{{user_id}}', to_jsonb(CAST(:new_id AS text))) "
                    f"WHERE {column}->>'user_id' = :old_id"
                ),
                params,
            )

        # assistants.system_prompt_history is a JSONB array of {created_by, ...} entries;
        # only the entries whose embedded created_by.id matches are rewritten, element by element.
        session.execute(
            text(
                """
                UPDATE assistants
                SET system_prompt_history = (
                    SELECT jsonb_agg(
                        CASE
                            WHEN elem->'created_by'->>'id' = :old_id
                                THEN jsonb_set(elem, '{created_by,id}', to_jsonb(CAST(:new_id AS text)))
                            ELSE elem
                        END
                    )
                    FROM jsonb_array_elements(system_prompt_history) AS elem
                )
                WHERE system_prompt_history IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM jsonb_array_elements(system_prompt_history) e
                      WHERE e->'created_by'->>'id' = :old_id
                  )
                """
            ),
            params,
        )


# Singleton instance (matches UserManagementService's convention)
user_remap_service = UserRemapService()
