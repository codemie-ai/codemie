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

"""Move or copy integrations between projects."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import status
from sqlalchemy import text
from sqlmodel import select

from codemie.clients.postgres import get_session
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.application_repository import application_repository
from codemie.rest_api.models.settings import PROJECT_NAME_TERM, CredentialValues, Settings
from codemie.rest_api.models.settings_transfer import (
    TransferMode,
    TransferItem,
    TransferSettingsResponse,
)
from codemie.service.settings.scheduler_settings_service import DATASOURCE_SCHEDULE_ALIAS_PREFIX
from codemie.service.settings.settings import SettingsService
from codemie_tools.base.models import CredentialTypes


class SettingsTransferService:
    """Move or copy every transferable integration from one project to another."""

    # Mirrors codemie.enterprise.litellm.budget_provider_adapter._PROJECT_KEY_ALIAS_PREFIX.
    # Redeclared rather than imported because that name is private to the enterprise package;
    LITELLM_PROJECT_KEY_ALIAS_PREFIX = "codemie:project:"
    COPY_BLOCKED_TYPES = (CredentialTypes.WEBHOOK, CredentialTypes.SCHEDULER)

    ADVISORY_LOCK_ID = int.from_bytes(
        hashlib.sha256(b"codemie:settings_transfer").digest()[:8], byteorder="big", signed=True
    )

    # Alias prefixes owned by other subsystems; rows carrying them are never transferred.
    SUBSYSTEM_MANAGED_PREFIXES = (
        SettingsService.INTERNAL_PREFIX,
        LITELLM_PROJECT_KEY_ALIAS_PREFIX,
        DATASOURCE_SCHEDULE_ALIAS_PREFIX,
    )

    @classmethod
    def _is_subsystem_managed(cls, alias: str | None) -> bool:
        """Rows written and owned by other subsystems are never transferred."""
        if not alias:
            return False
        if alias == SettingsService.ENFORCE_MEMBER_SPEND_LIMITS_ALIAS:
            return True

        return any(alias.startswith(prefix) for prefix in cls.SUBSYSTEM_MANAGED_PREFIXES)

    @classmethod
    def _select_candidates(cls, source_project_name: str) -> list[Settings]:
        """All integrations attached to the source project, both scopes, minus subsystem-managed rows."""
        rows = Settings.get_all_by_fields({PROJECT_NAME_TERM: source_project_name})
        candidates = [row for row in rows if not cls._is_subsystem_managed(row.alias)]

        logger.info(
            "settings_transfer: selected %s of %s rows in project %r",
            len(candidates),
            len(rows),
            source_project_name,
        )

        return candidates

    @classmethod
    def _partition(cls, candidates: list[Settings], mode: TransferMode) -> tuple[list[Settings], list[Settings]]:
        """Split candidates into (transferable, skipped). Only copy mode ever skips."""
        if mode is not TransferMode.COPY:
            return list(candidates), []

        transferable: list[Settings] = []
        skipped: list[Settings] = []
        for row in candidates:
            if row.credential_type in cls.COPY_BLOCKED_TYPES:
                skipped.append(row)
            else:
                transferable.append(row)

        return transferable, skipped

    @classmethod
    def _validate_projects(cls, source_project_name: str, target_project_name: str) -> None:
        """Both projects must exist and differ. Never auto-creates a missing project."""
        if source_project_name == target_project_name:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Invalid transfer request",
                details="Source and target projects must be different.",
                help="Choose a target project other than the source project.",
            )

        with get_session() as session:
            for label, name in (("Source", source_project_name), ("Target", target_project_name)):
                if application_repository.get_active_by_name(session, name) is None:
                    raise ExtendedHTTPException(
                        code=status.HTTP_404_NOT_FOUND,
                        message="Project not found",
                        details=f"{label} project '{name}' does not exist.",
                        help="Check the project name and try again.",
                    )

    @classmethod
    def _validate_aliases_present(cls, candidates: list[Settings]) -> None:
        """Every candidate needs an alias.

        Runs over all candidates, not just the transferable ones: skipped rows are rendered into
        TransferItem, whose alias field is required, so an alias-less trigger row would
        otherwise fail response construction with a 500 instead of this actionable 422.
        """
        blank = [row for row in candidates if not row.alias]
        if not blank:
            return

        details = ", ".join(f"{row.id} ({row.credential_type})" for row in blank)
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Integration without an alias",
            details=f"These integrations have no alias and cannot be transferred: {details}.",
            help="Give each integration an alias before transferring the project.",
        )

    @classmethod
    def _validate_no_collisions(cls, candidates: list[Settings], target_project_name: str) -> None:
        """Reuse the create-path uniqueness rule so a transfer cannot produce a forbidden state."""
        colliding = []
        for row in candidates:
            try:
                Settings.check_alias_unique(
                    project_name=target_project_name,
                    alias=row.alias,
                    setting_id=None,
                    user_id=row.user_id,
                    setting_type=row.setting_type,
                )
            except ValueError:
                colliding.append(row.alias)

        if colliding:
            raise ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message="Alias conflict in target project",
                details=(
                    f"Target project '{target_project_name}' already has integrations with these aliases: "
                    f"{', '.join(colliding)}."
                ),
                help=(
                    f"Rename or remove the conflicting integrations in target project "
                    f"'{target_project_name}', or rename them in the source project, then retry. "
                    f"A conflicting integration may be project-scoped or owned by another user."
                ),
            )

    @classmethod
    def _lock_source_rows(cls, candidates: list[Settings], source_project_name: str, session) -> list[Settings]:
        """Re-read every candidate FOR UPDATE and confirm it is still in the source project.

        Existence alone is not enough: a concurrent transfer may already have moved the row out,
        and re-applying this one would move a row the caller never selected.
        """
        ids = [row.id for row in candidates]
        locked = session.exec(select(Settings).where(Settings.id.in_(ids)).with_for_update()).all()
        by_id = {row.id: row for row in locked}

        rows: list[Settings] = []
        for candidate in candidates:
            row = by_id.get(candidate.id)
            if row is None:
                raise ExtendedHTTPException(
                    code=status.HTTP_409_CONFLICT,
                    message="Integration changed during transfer",
                    details=f"Integration '{candidate.alias}' no longer exists. No changes were applied.",
                    help="Retry the transfer.",
                )
            if row.project_name != source_project_name:
                raise ExtendedHTTPException(
                    code=status.HTTP_409_CONFLICT,
                    message="Integration changed during transfer",
                    details=(
                        f"Integration '{candidate.alias}' is no longer in project "
                        f"'{source_project_name}'. No changes were applied."
                    ),
                    help="Retry the transfer.",
                )
            rows.append(row)

        return rows

    @classmethod
    def _clone(cls, row: Settings, target_project_name: str, now: datetime) -> Settings:
        """Verbatim clone apart from id, project and timestamps. Ciphertext is carried over as-is."""
        return Settings(
            id=str(uuid4()),
            project_name=target_project_name,
            alias=row.alias,
            credential_type=row.credential_type,
            credential_values=[CredentialValues(key=cred.key, value=cred.value) for cred in row.credential_values],
            user_id=row.user_id,
            created_by=row.created_by,
            setting_type=row.setting_type,
            is_global=row.is_global,
            default=row.default,
            setting_hash=row.setting_hash,
            date=now,
            update_date=now,
        )

    @classmethod
    def _apply(
        cls, candidates: list[Settings], source_project_name: str, target_project_name: str, mode: TransferMode
    ) -> tuple[list[TransferItem], bool]:
        """Validate and write every row inside one locked transaction so the operation is atomic.

        Collision validation belongs here, not in the caller: outside this transaction it is a
        check-then-act that two concurrent transfers can both pass before either writes.

        Returns plain DTOs plus a "a LiteLLM row was written" flag rather than ORM rows: the
        session uses expire_on_commit=True and closes on block exit, so any attribute read on a
        returned row would raise DetachedInstanceError after a successful commit.
        """
        now = datetime.now(UTC)
        transferred: list[TransferItem] = []
        has_litellm = False

        with get_session() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": cls.ADVISORY_LOCK_ID})

            rows = cls._lock_source_rows(candidates, source_project_name, session)
            cls._validate_no_collisions(rows, target_project_name)

            for row in rows:
                if mode is TransferMode.MOVE:
                    row.project_name = target_project_name
                    row.update_date = now
                    session.add(row)
                    written = row
                else:
                    written = cls._clone(row, target_project_name, now)
                    session.add(written)

                # Materialize while the instances are still live; commit expires them.
                transferred.append(
                    TransferItem(
                        id=written.id,
                        alias=written.alias,
                        credential_type=written.credential_type,
                    )
                )
                has_litellm = has_litellm or written.credential_type == CredentialTypes.LITE_LLM

            session.commit()

        return transferred, has_litellm

    @classmethod
    def transfer(
        cls, source_project_name: str, target_project_name: str, mode: TransferMode
    ) -> TransferSettingsResponse:
        """Move or copy every transferable integration from the source project to the target project."""
        cls._validate_projects(source_project_name, target_project_name)

        candidates = cls._select_candidates(source_project_name)
        cls._validate_aliases_present(candidates)

        transferable, skipped = cls._partition(candidates, mode)
        skipped_payload = [
            TransferItem(id=row.id, alias=row.alias, credential_type=row.credential_type) for row in skipped
        ]

        if not transferable:
            return TransferSettingsResponse(
                message=f"No transferable integrations found in project '{source_project_name}'.",
                source_project_name=source_project_name,
                target_project_name=target_project_name,
                mode=mode,
                transferred_count=0,
                transferred=[],
                skipped_count=len(skipped_payload),
                skipped=skipped_payload,
            )

        transferred_payload, has_litellm = cls._apply(transferable, source_project_name, target_project_name, mode)

        if has_litellm:
            from codemie.enterprise.litellm.credentials import clear_litellm_user_credentials_cache

            clear_litellm_user_credentials_cache(None)

        logger.info(
            "settings_transfer: %s %s integration(s) from %r to %r (%s skipped)",
            mode.value,
            len(transferred_payload),
            source_project_name,
            target_project_name,
            len(skipped_payload),
        )

        return TransferSettingsResponse(
            message=(
                f"Transferred {len(transferred_payload)} integration(s) from "
                f"'{source_project_name}' to '{target_project_name}'."
            ),
            source_project_name=source_project_name,
            target_project_name=target_project_name,
            mode=mode,
            transferred_count=len(transferred_payload),
            transferred=transferred_payload,
            skipped_count=len(skipped_payload),
            skipped=skipped_payload,
        )
