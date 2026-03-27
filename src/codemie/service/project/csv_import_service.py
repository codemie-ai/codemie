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

from sqlmodel import Session

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.user_repository import user_repository
from codemie.rest_api.security.user import User
from codemie.service.project.project_assignment_service import project_assignment_service

COLUMNS = SimpleNamespace(EMAIL="email", ROLE="role")

ERRORS = SimpleNamespace(
    DECODE_FAILED="CSV decoding failed",
    VALIDATION_FAILED="CSV validation failed",
    INVALID_EMAIL="Invalid email address: '{}'",
    INVALID_ROLE="Invalid role '{}'. Allowed values: {}",
    DUPLICATE_EMAIL="Duplicate email: '{}' appears more than once",
    USER_NOT_FOUND="User not found for email: '{}'",
    MISSING_EMAIL_COLUMN="CSV must contain an '{}' column",
    EMPTY_CSV="CSV file must contain at least one data row",
    TOO_MANY_ROWS="CSV exceeds the maximum of {} rows (got {})",
    DECODE_DETAILS="File must be UTF-8 encoded: {}",
)

ALLOWED_ROLES: frozenset[str] = frozenset({"project_admin", "user"})
ROLE_TO_IS_ADMIN: dict[str, bool] = {"project_admin": True, "user": False}
DEFAULT_ROLE: str = "user"
MAX_ROWS: int = 1000
MAX_CSV_BYTES: int = 5 * 1024 * 1024  # 5 MB


class CsvImportService:
    """Parse a CSV file and bulk-assign users to a project."""

    def validate_csv(self, session: Session, content: bytes) -> list[dict]:
        """Validate CSV without importing. Returns per-row {email, role, error}.

        Structural errors (decode failure, missing column, empty/oversized CSV) raise 422.
        Row-level errors (bad email, bad role, user not found) are returned inline.
        """
        results = self._parse_and_validate(session, content)
        return [{"email": r["email"], "role": r["role"], "error": r["error"]} for r in results]

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
        results = self._parse_and_validate(session, content)

        row_errors = [{"row": i + 1, "reason": r["error"]} for i, r in enumerate(results) if r["error"]]
        if row_errors:
            logger.warning(
                f"csv_import_validation_failed: project={project_name}, "
                f"error_count={len(row_errors)}, by={actor.id}"
            )
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.VALIDATION_FAILED,
                details={"validation_errors": row_errors},
            )

        users = [{"user_id": r["user_id"], "is_project_admin": ROLE_TO_IS_ADMIN[r["role"]]} for r in results]
        return project_assignment_service.bulk_assign_users_to_project(
            session=session,
            project=project,
            users=users,
            project_name=project_name,
            actor=actor,
            action=action,
        )

    def _parse_and_validate(self, session: Session, content: bytes) -> list[dict]:
        """Core pipeline: decode → structural checks → per-row validation → DB email lookup.

        Structural errors (decode failure, missing column, empty/oversized CSV) raise 422.
        Row-level errors (bad email, bad role, user not found) are returned inline.

        Each result dict: {email, role, error (str | None), user_id (str | None)}
        """
        text = self._decode(content)
        reader = csv.DictReader(StringIO(text))
        self._validate_columns(reader)
        rows = list(reader)
        self._validate_row_count(rows)

        results, valid_emails = self._build_row_results(rows)
        if valid_emails:
            self._resolve_db_users(session, results, valid_emails)
        return results

    def _build_row_results(self, rows: list) -> tuple[list[dict], list[str]]:
        """Validate each row and return results list plus format-valid email list."""
        results: list[dict] = []
        valid_emails: list[str] = []
        seen_emails: set[str] = set()

        for row in rows:
            email = (row.get(COLUMNS.EMAIL) or "").strip().lower()
            role = (row.get(COLUMNS.ROLE) or "").strip() or DEFAULT_ROLE
            errors = self._validate_row(email, role, seen_emails)
            error = "; ".join(errors) if errors else None
            results.append({"email": email, "role": role, "error": error, "user_id": None})
            if not errors:
                seen_emails.add(email)
                valid_emails.append(email)

        return results, valid_emails

    @staticmethod
    def _validate_row(email: str, role: str, seen_emails: set[str]) -> list[str]:
        """Return a list of validation error strings for a single row."""
        errors: list[str] = []

        if role not in ALLOWED_ROLES:
            errors.append(ERRORS.INVALID_ROLE.format(role, sorted(ALLOWED_ROLES)))

        if not errors and email in seen_emails:
            errors.append(ERRORS.DUPLICATE_EMAIL.format(email))

        return errors

    @staticmethod
    def _resolve_db_users(session: Session, results: list[dict], valid_emails: list[str]) -> None:
        """Single DB round-trip: populate user_id for valid rows or set not-found error."""
        found = user_repository.get_by_emails(session, valid_emails)
        for row in results:
            if row["error"] is not None:
                continue
            db_user = found.get(row["email"])
            if db_user:
                row["user_id"] = db_user.id
            else:
                row["error"] = ERRORS.USER_NOT_FOUND.format(row["email"])

    @staticmethod
    def _decode(content: bytes) -> str:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.DECODE_FAILED,
                details=ERRORS.DECODE_DETAILS.format(exc),
            )

    @staticmethod
    def _validate_columns(reader: csv.DictReader) -> None:
        if reader.fieldnames is None or COLUMNS.EMAIL not in reader.fieldnames:
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.VALIDATION_FAILED,
                details=ERRORS.MISSING_EMAIL_COLUMN.format(COLUMNS.EMAIL),
            )

    @staticmethod
    def _validate_row_count(rows: list) -> None:
        if not rows:
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.VALIDATION_FAILED,
                details=ERRORS.EMPTY_CSV,
            )
        if len(rows) > MAX_ROWS:
            raise ExtendedHTTPException(
                code=422,
                message=ERRORS.VALIDATION_FAILED,
                details=ERRORS.TOO_MANY_ROWS.format(MAX_ROWS, len(rows)),
            )

    @staticmethod
    def _validate_rows(rows: list) -> list[dict]:
        validation_errors: list[dict] = []
        valid_rows: list[dict] = []

        for idx, row in enumerate(rows, start=1):
            email = (row.get(COLUMNS.EMAIL) or "").strip()
            role = (row.get(COLUMNS.ROLE) or "").strip() or DEFAULT_ROLE
            row_errors: list[str] = []

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
