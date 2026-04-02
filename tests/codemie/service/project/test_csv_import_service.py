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

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.rest_api.models.user_management import UserDB
from codemie.service.project.csv_import_service import CsvImportService, MAX_ROWS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csv(rows: list[str], header: str = "email,role") -> bytes:
    """Build CSV bytes from a header line and data rows."""
    lines = [header] + rows
    return "\n".join(lines).encode("utf-8")


def _make_user(email: str) -> UserDB:
    return UserDB(id=str(uuid4()), email=email, username=email.split("@")[0])


# ---------------------------------------------------------------------------
# TestCsvImportServiceParsing — _parse private logic via assign_from_csv
# ---------------------------------------------------------------------------


class TestCsvImportServiceParsing:
    """Unit tests for CsvImportService parsing logic."""

    service = CsvImportService()

    def _parse(self, content: bytes) -> list[dict]:
        """Invoke the private _parse method directly for parse-only tests."""
        return self.service._parse(content)

    def test_parse_valid_csv(self):
        """Happy path — both roles parsed and mapped correctly."""
        content = _csv(
            [
                "admin@example.com,administrator",
                "user@example.com,user",
            ]
        )

        result = self._parse(content)

        assert len(result) == 2
        assert result[0] == {"email": "admin@example.com", "role": "administrator", "is_project_admin": True}
        assert result[1] == {"email": "user@example.com", "role": "user", "is_project_admin": False}

    def test_parse_invalid_role(self):
        """Row with unknown role → 422 with allowed roles listed."""
        content = _csv(["valid@example.com,superuser"])

        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._parse(content)

        exc = exc_info.value
        assert exc.code == 422
        errors = exc.details["validation_errors"]
        assert len(errors) == 1
        assert errors[0]["row"] == 1
        assert "administrator" in errors[0]["reason"]
        assert "user" in errors[0]["reason"]

    def test_parse_multiple_errors(self):
        """All row errors are collected before raising — not first-fail-fast."""
        content = _csv(
            [
                "bad-email,invalid-role",
                "also-bad,another-bad",
            ]
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._parse(content)

        errors = exc_info.value.details["validation_errors"]
        assert len(errors) == 2

    def test_parse_missing_email_column(self):
        """CSV with no 'email' column → 422."""
        content = b"role\nadministrator\n"

        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._parse(content)

        exc = exc_info.value
        assert exc.code == 422
        assert isinstance(exc.details, str) and "email" in exc.details.lower()

    def test_parse_role_column_optional(self):
        """CSV without 'role' column → defaults to 'user' role."""
        content = b"email\nfoo@example.com\n"

        result = self._parse(content)

        assert len(result) == 1
        assert result[0]["email"] == "foo@example.com"
        assert result[0]["role"] == "user"
        assert result[0]["is_project_admin"] is False

    def test_parse_empty_role_value_defaults(self):
        """Row with empty role value → defaults to 'user' role."""
        content = b"email,role\nfoo@example.com,\n"

        result = self._parse(content)

        assert result[0]["role"] == "user"
        assert result[0]["is_project_admin"] is False

    def test_parse_empty_csv(self):
        """Header only, no data rows → 422."""
        content = b"email,role\n"

        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._parse(content)

        exc = exc_info.value
        assert exc.code == 422
        assert isinstance(exc.details, str) and "at least one" in exc.details.lower()

    def test_parse_too_many_rows(self):
        """More than MAX_ROWS rows → 422."""
        content = _csv([f"user{i}@example.com,user" for i in range(MAX_ROWS + 1)])

        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._parse(content)

        exc = exc_info.value
        assert exc.code == 422
        assert isinstance(exc.details, str) and str(MAX_ROWS) in exc.details

    def test_parse_decode_error(self):
        """Non-UTF-8 bytes → 422."""
        content = b"\xff\xfe invalid bytes"

        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._parse(content)

        exc = exc_info.value
        assert exc.code == 422
        assert "utf-8" in exc.message.lower() or (isinstance(exc.details, str) and "utf-8" in exc.details.lower())


# ---------------------------------------------------------------------------
# TestCsvImportServiceAssign — assign_from_csv full flow
# ---------------------------------------------------------------------------


class TestCsvImportServiceAssign:
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
    def test_assign_from_csv_success(self, mock_user_repo, mock_assignment_service):
        """Valid CSV, all users found → delegates to bulk_assign_users_to_project."""
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

        content = _csv(["admin@example.com,administrator", "user@example.com,user"])

        results = self.service.assign_from_csv(
            session=session,
            content=content,
            project=project,
            project_name=project.name,
            actor=actor,
            action="POST /v1/projects/my-project/import-users",
        )

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
    def test_assign_from_csv_user_not_found(self, mock_user_repo, mock_assignment_service):
        """One email not in DB → 422 with human-readable details."""
        project = self._make_project()
        actor = self._make_actor()
        session = MagicMock()

        existing_user = _make_user("exists@example.com")
        mock_user_repo.get_by_emails.return_value = {"exists@example.com": existing_user}

        content = _csv(["exists@example.com,user", "missing@example.com,administrator"])

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
        assert "could not be found" in exc.message.lower()
        assert isinstance(exc.details, str)
        assert "missing@example.com" in exc.details
        mock_assignment_service.bulk_assign_users_to_project.assert_not_called()

    @patch("codemie.service.project.csv_import_service.project_assignment_service")
    @patch("codemie.service.project.csv_import_service.user_repository")
    def test_assign_from_csv_all_not_found(self, mock_user_repo, mock_assignment_service):
        """All emails missing from DB → 422 with all emails listed."""
        project = self._make_project()
        actor = self._make_actor()
        session = MagicMock()

        mock_user_repo.get_by_emails.return_value = {}

        content = _csv(["ghost1@example.com,user", "ghost2@example.com,administrator"])

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
        assert isinstance(exc.details, str)
        assert "ghost1@example.com" in exc.details
        assert "ghost2@example.com" in exc.details
        mock_assignment_service.bulk_assign_users_to_project.assert_not_called()
