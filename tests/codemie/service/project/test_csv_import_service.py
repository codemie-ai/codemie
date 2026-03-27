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

"""Unit tests for CsvImportService — _parse_and_validate, validate_csv, and assign_from_csv."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.rest_api.models.user_management import UserDB
from codemie.service.project.csv_import_service import (
    CsvImportService,
    ERRORS,
    MAX_ROWS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csv(rows: list[str], header: str = "email,role") -> bytes:
    """Build UTF-8 CSV bytes from a header line and data rows."""
    lines = [header] + rows
    return "\n".join(lines).encode("utf-8")


def _make_user(email: str) -> UserDB:
    return UserDB(id=str(uuid4()), email=email, username=email.split("@")[0])


# ---------------------------------------------------------------------------
# TestParseAndValidate — _parse_and_validate shared core pipeline
# ---------------------------------------------------------------------------


class TestParseAndValidate:
    """Unit tests for CsvImportService._parse_and_validate() core pipeline."""

    service = CsvImportService()

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_valid_rows_return_correct_shape(self, mock_user_repo):
        """Happy path — valid email and role produce correct output dict."""
        # Arrange
        admin_user = _make_user("admin@example.com")
        regular_user = _make_user("user@example.com")
        mock_user_repo.get_by_emails.return_value = {
            "admin@example.com": admin_user,
            "user@example.com": regular_user,
        }
        session = MagicMock()
        content = _csv(
            [
                "admin@example.com,project_admin",
                "user@example.com,user",
            ]
        )

        # Act
        results = self.service._parse_and_validate(session, content)

        # Assert
        assert len(results) == 2
        assert results[0] == {
            "email": "admin@example.com",
            "role": "project_admin",
            "error": None,
            "user_id": admin_user.id,
        }
        assert results[1] == {
            "email": "user@example.com",
            "role": "user",
            "error": None,
            "user_id": regular_user.id,
        }

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_invalid_role_produces_inline_error(self, mock_user_repo):
        """Row with unrecognised role → error field set inline with allowed values."""
        # Arrange
        mock_user_repo.get_by_emails.return_value = {}
        session = MagicMock()
        content = _csv(["valid@example.com,superuser"])

        # Act
        results = self.service._parse_and_validate(session, content)

        # Assert
        assert len(results) == 1
        assert results[0]["error"] is not None
        assert "superuser" in results[0]["error"]
        assert "project_admin" in results[0]["error"]
        assert "user" in results[0]["error"]
        assert results[0]["user_id"] is None

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_invalid_role_error_reported_inline(self, mock_user_repo):
        """Row with invalid role → role error reported inline."""
        # Arrange
        mock_user_repo.get_by_emails.return_value = {}
        session = MagicMock()
        content = _csv(["not-an-email,superuser"])

        # Act
        results = self.service._parse_and_validate(session, content)

        # Assert
        assert len(results) == 1
        error = results[0]["error"]
        assert error is not None
        assert "superuser" in error

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_email_not_found_in_db_produces_inline_error(self, mock_user_repo):
        """Format-valid email that has no DB record → error field set inline."""
        # Arrange
        mock_user_repo.get_by_emails.return_value = {}  # nobody found
        session = MagicMock()
        content = _csv(["ghost@example.com,user"])

        # Act
        results = self.service._parse_and_validate(session, content)

        # Assert
        assert len(results) == 1
        assert results[0]["error"] is not None
        assert "ghost@example.com" in results[0]["error"]
        assert results[0]["user_id"] is None

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_mix_of_valid_and_invalid_rows(self, mock_user_repo):
        """Mix of good and bad rows — valid rows resolved, invalid rows get errors."""
        # Arrange
        good_user = _make_user("good@example.com")
        mock_user_repo.get_by_emails.return_value = {"good@example.com": good_user}
        session = MagicMock()
        content = _csv(
            [
                "good@example.com,user",
                "bad-email,superuser",
                "missing@example.com,project_admin",
            ]
        )
        # missing@example.com is format-valid but not in the DB mock

        # Act
        results = self.service._parse_and_validate(session, content)

        # Assert
        assert len(results) == 3
        # Row 0: valid email + valid role + found in DB
        assert results[0]["error"] is None
        assert results[0]["user_id"] == good_user.id
        # Row 1: invalid email + invalid role
        assert results[1]["error"] is not None
        assert results[1]["user_id"] is None
        # Row 2: valid email + valid role but not in DB
        assert results[2]["error"] is not None
        assert "missing@example.com" in results[2]["error"]
        assert results[2]["user_id"] is None

    def test_non_utf8_bytes_raises_422(self):
        """Non-UTF-8 byte sequence → structural 422, decode failed message."""
        # Arrange
        session = MagicMock()
        content = b"\xff\xfe invalid bytes"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service._parse_and_validate(session, content)

        exc = exc_info.value
        assert exc.code == 422
        assert ERRORS.DECODE_FAILED in exc.message

    def test_missing_email_column_raises_422(self):
        """CSV without an 'email' column → structural 422."""
        # Arrange
        session = MagicMock()
        content = b"role\nproject_admin\n"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service._parse_and_validate(session, content)

        exc = exc_info.value
        assert exc.code == 422
        assert ERRORS.VALIDATION_FAILED in exc.message
        assert isinstance(exc.details, str)
        assert "email" in exc.details.lower()

    def test_header_only_csv_raises_422(self):
        """CSV with header but no data rows → structural 422."""
        # Arrange
        session = MagicMock()
        content = b"email,role\n"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service._parse_and_validate(session, content)

        exc = exc_info.value
        assert exc.code == 422
        assert ERRORS.VALIDATION_FAILED in exc.message
        assert isinstance(exc.details, str)
        assert "at least one" in exc.details.lower()

    def test_exceeding_max_rows_raises_422(self):
        """More than MAX_ROWS data rows → structural 422."""
        # Arrange
        session = MagicMock()
        content = _csv([f"user{i}@example.com,user" for i in range(MAX_ROWS + 1)])

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service._parse_and_validate(session, content)

        exc = exc_info.value
        assert exc.code == 422
        assert ERRORS.VALIDATION_FAILED in exc.message
        assert isinstance(exc.details, str)
        assert str(MAX_ROWS) in exc.details

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_blank_role_defaults_to_user(self, mock_user_repo):
        """Row with an empty role cell → defaults to 'user'."""
        # Arrange
        found_user = _make_user("foo@example.com")
        mock_user_repo.get_by_emails.return_value = {"foo@example.com": found_user}
        session = MagicMock()
        content = b"email,role\nfoo@example.com,\n"

        # Act
        results = self.service._parse_and_validate(session, content)

        # Assert
        assert len(results) == 1
        assert results[0]["role"] == "user"
        assert results[0]["error"] is None

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_missing_role_column_defaults_to_user(self, mock_user_repo):
        """CSV without a 'role' column at all → role defaults to 'user'."""
        # Arrange
        found_user = _make_user("foo@example.com")
        mock_user_repo.get_by_emails.return_value = {"foo@example.com": found_user}
        session = MagicMock()
        content = b"email\nfoo@example.com\n"

        # Act
        results = self.service._parse_and_validate(session, content)

        # Assert
        assert len(results) == 1
        assert results[0]["role"] == "user"
        assert results[0]["error"] is None

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_all_format_invalid_rows_skip_db_lookup(self, mock_user_repo):
        """When every row is format-invalid, the DB lookup is skipped entirely."""
        # Arrange
        session = MagicMock()
        content = _csv(["bad-email,superuser"])

        # Act
        self.service._parse_and_validate(session, content)

        # Assert — DB not queried at all
        mock_user_repo.get_by_emails.assert_not_called()

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_duplicate_email_produces_inline_error(self, mock_user_repo):
        """Second occurrence of the same email → inline error, not sent to DB twice."""
        # Arrange
        found_user = _make_user("alice@example.com")
        mock_user_repo.get_by_emails.return_value = {"alice@example.com": found_user}
        session = MagicMock()
        content = _csv(["alice@example.com,user", "alice@example.com,project_admin"])

        # Act
        results = self.service._parse_and_validate(session, content)

        # Assert — first row is valid, second gets a duplicate error
        assert len(results) == 2
        assert results[0]["error"] is None
        assert results[0]["user_id"] == found_user.id
        assert results[1]["error"] is not None
        assert "duplicate" in results[1]["error"].lower()
        assert "alice@example.com" in results[1]["error"]
        # DB should only receive the first (unique) email
        called_emails = mock_user_repo.get_by_emails.call_args[0][1]
        assert called_emails.count("alice@example.com") == 1

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_mixed_case_email_normalised_to_lowercase(self, mock_user_repo):
        """Emails are lowercased at capture so DB lookup and result dict are consistent."""
        # Arrange
        found_user = _make_user("alice@example.com")
        mock_user_repo.get_by_emails.return_value = {"alice@example.com": found_user}
        session = MagicMock()
        content = _csv(["Alice@Example.COM,user"])

        # Act
        results = self.service._parse_and_validate(session, content)

        # Assert — email in result is lowercase and DB resolved correctly
        assert results[0]["email"] == "alice@example.com"
        assert results[0]["error"] is None
        assert results[0]["user_id"] == found_user.id


# ---------------------------------------------------------------------------
# TestValidateCsv — validate_csv public surface
# ---------------------------------------------------------------------------


class TestValidateCsv:
    """Unit tests for CsvImportService.validate_csv() — dry-run validation."""

    service = CsvImportService()

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_returns_email_role_error_per_row(self, mock_user_repo):
        """validate_csv strips user_id and returns only {email, role, error}."""
        # Arrange
        found_user = _make_user("ok@example.com")
        mock_user_repo.get_by_emails.return_value = {"ok@example.com": found_user}
        session = MagicMock()
        content = _csv(["ok@example.com,project_admin"])

        # Act
        results = self.service.validate_csv(session=session, content=content)

        # Assert
        assert len(results) == 1
        row = results[0]
        assert set(row.keys()) == {"email", "role", "error"}
        assert row["email"] == "ok@example.com"
        assert row["role"] == "project_admin"
        assert row["error"] is None

    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_mixed_rows_return_errors_inline(self, mock_user_repo):
        """validate_csv returns None error for valid rows, error string for bad rows."""
        # Arrange
        good_user = _make_user("valid@example.com")
        mock_user_repo.get_by_emails.return_value = {"valid@example.com": good_user}
        session = MagicMock()
        content = _csv(
            [
                "valid@example.com,user",
                "bad-email,superuser",
            ]
        )

        # Act
        results = self.service.validate_csv(session=session, content=content)

        # Assert
        assert len(results) == 2
        assert results[0]["error"] is None
        assert results[1]["error"] is not None

    def test_structural_error_propagates_as_422(self):
        """validate_csv propagates structural 422 from _parse_and_validate."""
        # Arrange
        session = MagicMock()
        content = b"\xff\xfe invalid"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service.validate_csv(session=session, content=content)

        assert exc_info.value.code == 422


# ---------------------------------------------------------------------------
# TestAssignFromCsv — assign_from_csv full flow
# ---------------------------------------------------------------------------


class TestAssignFromCsv:
    """Unit tests for CsvImportService.assign_from_csv()."""

    service = CsvImportService()

    def _make_project(self, name: str = "my-project") -> Application:
        return Application(id=str(uuid4()), name=name, project_type="team")

    def _make_actor(self) -> MagicMock:
        actor = MagicMock()
        actor.id = str(uuid4())
        actor.is_admin = True
        return actor

    @patch("codemie.service.project.csv_import_service.project_assignment_service")
    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_all_valid_rows_delegates_to_bulk_assign(self, mock_user_repo, mock_assignment_service):
        """All rows valid → calls bulk_assign_users_to_project with correct args."""
        # Arrange
        project = self._make_project()
        actor = self._make_actor()
        session = MagicMock()

        admin_user = _make_user("admin@example.com")
        regular_user = _make_user("user@example.com")

        mock_user_repo.get_by_emails.return_value = {
            "admin@example.com": admin_user,
            "user@example.com": regular_user,
        }
        expected_results = [
            {"user_id": admin_user.id, "action": "assigned", "is_project_admin": True},
            {"user_id": regular_user.id, "action": "assigned", "is_project_admin": False},
        ]
        mock_assignment_service.bulk_assign_users_to_project.return_value = expected_results

        content = _csv(["admin@example.com,project_admin", "user@example.com,user"])

        # Act
        results = self.service.assign_from_csv(
            session=session,
            content=content,
            project=project,
            project_name=project.name,
            actor=actor,
            action="POST /v1/projects/my-project/import-users",
        )

        # Assert
        assert results == expected_results
        mock_assignment_service.bulk_assign_users_to_project.assert_called_once_with(
            session=session,
            project=project,
            users=[
                {"user_id": admin_user.id, "is_project_admin": True},
                {"user_id": regular_user.id, "is_project_admin": False},
            ],
            project_name=project.name,
            actor=actor,
            action="POST /v1/projects/my-project/import-users",
        )

    @patch("codemie.service.project.csv_import_service.project_assignment_service")
    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_single_row_error_raises_422_without_bulk_assign(self, mock_user_repo, mock_assignment_service):
        """Any row error → raises single 422, bulk_assign is NOT called."""
        # Arrange
        project = self._make_project()
        actor = self._make_actor()
        session = MagicMock()

        # email format is invalid, DB lookup should not matter
        mock_user_repo.get_by_emails.return_value = {}
        content = _csv(["not-an-email,user"])

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service.assign_from_csv(
                session=session,
                content=content,
                project=project,
                project_name=project.name,
                actor=actor,
                action="POST /v1/projects/my-project/import-users",
            )

        exc = exc_info.value
        assert exc.code == 422
        assert exc.message == ERRORS.VALIDATION_FAILED
        assert "validation_errors" in exc.details
        mock_assignment_service.bulk_assign_users_to_project.assert_not_called()

    @patch("codemie.service.project.csv_import_service.project_assignment_service")
    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_missing_user_raises_422_without_bulk_assign(self, mock_user_repo, mock_assignment_service):
        """Valid-format email not in DB → 422 with user email in details, no bulk assign."""
        # Arrange
        project = self._make_project()
        actor = self._make_actor()
        session = MagicMock()

        existing_user = _make_user("exists@example.com")
        mock_user_repo.get_by_emails.return_value = {"exists@example.com": existing_user}

        content = _csv(["exists@example.com,user", "missing@example.com,project_admin"])

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service.assign_from_csv(
                session=session,
                content=content,
                project=project,
                project_name=project.name,
                actor=actor,
                action="POST /v1/projects/my-project/import-users",
            )

        exc = exc_info.value
        assert exc.code == 422
        assert exc.message == ERRORS.VALIDATION_FAILED
        validation_errors = exc.details["validation_errors"]
        assert any("missing@example.com" in e["reason"] for e in validation_errors)
        mock_assignment_service.bulk_assign_users_to_project.assert_not_called()

    @patch("codemie.service.project.csv_import_service.project_assignment_service")
    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_all_row_errors_collected_in_single_422(self, mock_user_repo, mock_assignment_service):
        """Multiple invalid rows → all errors collected in one 422, not first-fail-fast."""
        # Arrange
        project = self._make_project()
        actor = self._make_actor()
        session = MagicMock()

        mock_user_repo.get_by_emails.return_value = {}

        content = _csv(
            [
                "bad-email-1,superuser",
                "bad-email-2,superuser",
            ]
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service.assign_from_csv(
                session=session,
                content=content,
                project=project,
                project_name=project.name,
                actor=actor,
                action="POST /v1/projects/my-project/import-users",
            )

        exc = exc_info.value
        assert exc.code == 422
        validation_errors = exc.details["validation_errors"]
        # Two rows → each has two errors (bad email + bad role), so at least 2 entries
        assert len(validation_errors) >= 2
        mock_assignment_service.bulk_assign_users_to_project.assert_not_called()

    @patch("codemie.service.project.csv_import_service.project_assignment_service")
    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_error_row_numbers_are_1_indexed(self, mock_user_repo, mock_assignment_service):
        """Row numbers in error details are 1-indexed (first data row = row 1)."""
        # Arrange
        project = self._make_project()
        actor = self._make_actor()
        session = MagicMock()

        mock_user_repo.get_by_emails.return_value = {}
        content = _csv(["bad-email,user"])

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service.assign_from_csv(
                session=session,
                content=content,
                project=project,
                project_name=project.name,
                actor=actor,
                action="POST /v1/projects/my-project/import-users",
            )

        validation_errors = exc_info.value.details["validation_errors"]
        assert len(validation_errors) == 1
        assert validation_errors[0]["row"] == 1

    @patch("codemie.service.project.csv_import_service.project_assignment_service")
    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_second_row_error_has_row_number_2(self, mock_user_repo, mock_assignment_service):
        """Row numbers reflect actual position — second bad row reports row=2."""
        # Arrange
        project = self._make_project()
        actor = self._make_actor()
        session = MagicMock()

        good_user = _make_user("good@example.com")
        mock_user_repo.get_by_emails.return_value = {"good@example.com": good_user}

        content = _csv(
            [
                "good@example.com,user",  # row 1 — valid
                "bad-email,user",  # row 2 — invalid email
            ]
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service.assign_from_csv(
                session=session,
                content=content,
                project=project,
                project_name=project.name,
                actor=actor,
                action="POST /v1/projects/my-project/import-users",
            )

        validation_errors = exc_info.value.details["validation_errors"]
        assert len(validation_errors) == 1
        assert validation_errors[0]["row"] == 2

    @patch("codemie.service.project.csv_import_service.project_assignment_service")
    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_structural_error_propagates_before_row_errors(self, mock_user_repo, mock_assignment_service):
        """Structural decode error propagates before row-level checks."""
        # Arrange
        project = self._make_project()
        actor = self._make_actor()
        session = MagicMock()
        content = b"\xff\xfe garbage"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self.service.assign_from_csv(
                session=session,
                content=content,
                project=project,
                project_name=project.name,
                actor=actor,
                action="POST /v1/projects/my-project/import-users",
            )

        assert exc_info.value.code == 422
        assert exc_info.value.message == ERRORS.DECODE_FAILED
        mock_assignment_service.bulk_assign_users_to_project.assert_not_called()
