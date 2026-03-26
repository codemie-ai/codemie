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

"""CSV import service — parse a CSV file and bulk-assign users to a project."""

from __future__ import annotations

import csv
from io import StringIO
from types import SimpleNamespace

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlmodel import Session

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.user_repository import user_repository
from codemie.rest_api.security.user import User
from codemie.service.project.project_assignment_service import project_assignment_service

_email_adapter: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)

COLUMNS = SimpleNamespace(EMAIL="email", ROLE="role")

ERRORS = SimpleNamespace(
    DECODE_FAILED="CSV decoding failed",
    VALIDATION_FAILED="CSV validation failed",
    USERS_NOT_FOUND="Some users could not be found",
)

ALLOWED_ROLES: frozenset[str] = frozenset({"administrator", "user"})
ROLE_TO_IS_ADMIN: dict[str, bool] = {"administrator": True, "user": False}
DEFAULT_ROLE: str = "user"
MAX_ROWS: int = 1000
MAX_CSV_BYTES: int = 5 * 1024 * 1024  # 5 MB


class CsvImportService:
    """Parse a CSV file and bulk-assign users to a project."""

    def assign_from_csv(
        self,
        session: Session,
        content: bytes,
        project: Application,
        project_name: str,
        actor: User,
        action: str,
    ) -> list[dict]:
        """Parse CSV content and bulk-assign users to the project.

        Args:
            session: Database session
            content: Raw bytes of the uploaded CSV file
            project: Authorized project from dependency
            project_name: Project name
            actor: User performing the request
            action: Action string for logging

        Returns:
            List of per-user result dicts (same shape as bulk_assign_users_to_project)

        Raises:
            ExtendedHTTPException(422): On CSV parse errors or unresolvable emails
        """
        parsed_rows = self._parse(content)
        users = self._resolve_emails(session, parsed_rows, project_name, actor)
        return project_assignment_service.bulk_assign_users_to_project(
            session=session,
            project=project,
            users=users,
            project_name=project_name,
            actor=actor,
            action=action,
        )

    # ------------------------------------------------------------------
    # Private — parsing
    # ------------------------------------------------------------------

    def _parse(self, content: bytes) -> list[dict]:
        """Decode and validate CSV bytes, returning a list of validated row dicts.

        Each returned dict has the keys: ``email``, ``role``, ``is_project_admin``.
        All row-level errors are collected before raising.
        """
        text = self._decode(content)
        reader = csv.DictReader(StringIO(text))
        self._validate_columns(reader)
        rows = list(reader)
        self._validate_row_count(rows)
        return self._validate_rows(rows)

    @staticmethod
    def _decode(content: bytes) -> str:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.DECODE_FAILED,
                details=f"File must be UTF-8 encoded: {exc}",
            )

    @staticmethod
    def _validate_columns(reader: csv.DictReader) -> None:
        if reader.fieldnames is None or COLUMNS.EMAIL not in reader.fieldnames:
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.VALIDATION_FAILED,
                details=f"CSV must contain an '{COLUMNS.EMAIL}' column",
            )

    @staticmethod
    def _validate_row_count(rows: list) -> None:
        if not rows:
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.VALIDATION_FAILED,
                details="CSV file must contain at least one data row",
            )
        if len(rows) > MAX_ROWS:
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.VALIDATION_FAILED,
                details=f"CSV exceeds the maximum of {MAX_ROWS} rows (got {len(rows)})",
            )

    @staticmethod
    def _validate_rows(rows: list) -> list[dict]:
        validation_errors: list[dict] = []
        valid_rows: list[dict] = []

        for idx, row in enumerate(rows, start=1):
            email = (row.get(COLUMNS.EMAIL) or "").strip()
            role = (row.get(COLUMNS.ROLE) or "").strip() or DEFAULT_ROLE
            row_errors: list[str] = []

            try:
                _email_adapter.validate_python(email)
            except ValidationError:
                row_errors.append(f"Invalid email address: '{email}'")

            if role not in ALLOWED_ROLES:
                row_errors.append(f"Invalid role '{role}'. Allowed values: {sorted(ALLOWED_ROLES)}")

            if row_errors:
                for reason in row_errors:
                    validation_errors.append({"row": idx, "reason": reason})
            else:
                valid_rows.append(
                    {
                        COLUMNS.EMAIL: email,
                        COLUMNS.ROLE: role,
                        "is_project_admin": ROLE_TO_IS_ADMIN[role],
                    }
                )

        if validation_errors:
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.VALIDATION_FAILED,
                details={"validation_errors": validation_errors},
            )

        return valid_rows

    def _resolve_emails(self, session: Session, parsed_rows: list[dict], project_name: str, actor: User) -> list[dict]:
        all_emails = [row[COLUMNS.EMAIL] for row in parsed_rows]
        email_to_user = user_repository.get_by_emails(session, all_emails)

        not_found: list[str] = []
        users: list[dict] = []

        for row in parsed_rows:
            email = row[COLUMNS.EMAIL]
            db_user = email_to_user.get(email.lower())
            if db_user is None:
                not_found.append(email)
            else:
                users.append({"user_id": db_user.id, "is_project_admin": row["is_project_admin"]})

        if not_found:
            logger.warning(f"csv_import_users_not_found: project={project_name}, count={len(not_found)}, by={actor.id}")
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.USERS_NOT_FOUND,
                details=f"The following email addresses are not registered in the system: {', '.join(not_found)}",
            )

        return users


csv_import_service = CsvImportService()
