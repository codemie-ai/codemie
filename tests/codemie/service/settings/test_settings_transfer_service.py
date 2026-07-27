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

"""Tests for the project integration transfer service and its request/response models."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from pydantic import ValidationError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.settings import CredentialValues, Settings, SettingType
from codemie.rest_api.models.settings_transfer import TransferMode, TransferSettingsRequest
from codemie.service.settings.settings_transfer_service import SettingsTransferService
from codemie_tools.base.models import CredentialTypes


def _setting(alias, credential_type=CredentialTypes.JIRA, setting_type=SettingType.PROJECT, user_id="u1", id_=None):
    return Settings(
        id=id_ or f"id-{alias}",
        project_name="source",
        alias=alias,
        credential_type=credential_type,
        credential_values=[],
        user_id=user_id,
        setting_type=setting_type,
    )


class TestTransferSettingsRequest:
    def test_accepts_move_and_copy(self):
        # Arrange / Act
        move = TransferSettingsRequest(source_project_name="x", target_project_name="y", mode="move")
        copy = TransferSettingsRequest(source_project_name="x", target_project_name="y", mode="copy")

        # Assert
        assert move.mode is TransferMode.MOVE
        assert copy.mode is TransferMode.COPY

    def test_rejects_unknown_mode(self):
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            TransferSettingsRequest(source_project_name="x", target_project_name="y", mode="teleport")

    def test_rejects_missing_mode(self):
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            TransferSettingsRequest(source_project_name="x", target_project_name="y")

    def test_rejects_blank_project_name(self):
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            TransferSettingsRequest(source_project_name="", target_project_name="y", mode="move")


class TestSelectCandidates:
    def test_litellm_prefix_matches_budget_provider_adapter(self):
        # Arrange
        from codemie.enterprise.litellm.budget_provider_adapter import _PROJECT_KEY_ALIAS_PREFIX

        # Assert
        assert SettingsTransferService.LITELLM_PROJECT_KEY_ALIAS_PREFIX == _PROJECT_KEY_ALIAS_PREFIX

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_drops_subsystem_managed_rows(self, mock_get_all):
        # Arrange
        keep = _setting("jira-prod")
        mock_get_all.return_value = [
            keep,
            _setting("__internal__IDE_abc"),
            _setting("codemie:project:source:category:premium_models", CredentialTypes.LITE_LLM),
            _setting("Schedule_my-datasource", CredentialTypes.SCHEDULER),
            _setting("project_member_budget_tracking_enabled", CredentialTypes.ENVIRONMENT_VARS),
        ]

        # Act
        result = SettingsTransferService._select_candidates("source")

        # Assert
        assert [s.alias for s in result] == ["jira-prod"]
        mock_get_all.assert_called_once_with({"project_name.keyword": "source"})

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_keeps_both_setting_types(self, mock_get_all):
        # Arrange
        mock_get_all.return_value = [
            _setting("project-cred", setting_type=SettingType.PROJECT),
            _setting("user-cred", setting_type=SettingType.USER),
        ]

        # Act
        result = SettingsTransferService._select_candidates("source")

        # Assert
        assert {s.alias for s in result} == {"project-cred", "user-cred"}


class TestPartition:
    def test_copy_skips_trigger_types(self):
        # Arrange
        jira = _setting("jira-prod", CredentialTypes.JIRA)
        hook = _setting("gitlab-hook", CredentialTypes.WEBHOOK)
        cron = _setting("nightly", CredentialTypes.SCHEDULER)

        # Act
        transferable, skipped = SettingsTransferService._partition([jira, hook, cron], TransferMode.COPY)

        # Assert
        assert [s.alias for s in transferable] == ["jira-prod"]
        assert {s.alias for s in skipped} == {"gitlab-hook", "nightly"}

    def test_move_skips_nothing(self):
        # Arrange
        rows = [
            _setting("jira-prod", CredentialTypes.JIRA),
            _setting("gitlab-hook", CredentialTypes.WEBHOOK),
            _setting("nightly", CredentialTypes.SCHEDULER),
        ]

        # Act
        transferable, skipped = SettingsTransferService._partition(rows, TransferMode.MOVE)

        # Assert
        assert len(transferable) == 3
        assert skipped == []


class TestValidateProjects:
    def test_rejects_same_source_and_target(self):
        # Arrange / Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_projects("same", "same")

        assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @patch("codemie.service.settings.settings_transfer_service.application_repository")
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    def test_rejects_missing_source_project(self, mock_get_session, mock_repo):
        # Arrange
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_repo.get_active_by_name.side_effect = [None, MagicMock()]

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_projects("missing", "target")

        assert excinfo.value.code == status.HTTP_404_NOT_FOUND
        assert "missing" in excinfo.value.details

    @patch("codemie.service.settings.settings_transfer_service.application_repository")
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    def test_rejects_soft_deleted_target_project(self, mock_get_session, mock_repo):
        # Arrange — get_active_by_name excludes soft-deleted rows, so the target resolves to None.
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_repo.get_active_by_name.side_effect = [MagicMock(), None]

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_projects("source", "target")

        assert excinfo.value.code == status.HTTP_404_NOT_FOUND


class TestValidateAliasesPresent:
    def test_passes_when_every_candidate_has_an_alias(self):
        # Arrange / Act / Assert — no exception
        SettingsTransferService._validate_aliases_present([_setting("jira-prod"), _setting("git-main")])

    def test_rejects_row_with_empty_alias(self):
        # Arrange
        row = _setting("placeholder")
        row.alias = None

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_aliases_present([row])

        assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert row.id in excinfo.value.details


class TestValidateNoCollisions:
    @patch("codemie.service.settings.settings_transfer_service.Settings.check_alias_unique")
    def test_passes_when_no_collisions(self, mock_check):
        # Arrange
        mock_check.return_value = True
        rows = [_setting("jira-prod"), _setting("git-main")]

        # Act
        SettingsTransferService._validate_no_collisions(rows, "target")

        # Assert
        assert mock_check.call_count == 2
        assert mock_check.call_args_list[0].kwargs["setting_id"] is None
        assert mock_check.call_args_list[0].kwargs["project_name"] == "target"

    @patch("codemie.service.settings.settings_transfer_service.Settings.check_alias_unique")
    def test_reports_every_colliding_alias(self, mock_check):
        # Arrange
        mock_check.side_effect = [ValueError("dup"), True, ValueError("dup")]
        rows = [_setting("a"), _setting("b"), _setting("c")]

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_no_collisions(rows, "target")

        assert excinfo.value.code == status.HTTP_409_CONFLICT
        assert "a" in excinfo.value.details
        assert "c" in excinfo.value.details

    @patch("codemie.service.settings.settings_transfer_service.Settings.check_alias_unique")
    def test_conflict_help_names_the_target_project(self, mock_check):
        # Arrange
        mock_check.side_effect = ValueError("dup")

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_no_collisions([_setting("jira-prod")], "target")

        assert "target" in excinfo.value.details
        assert "target" in excinfo.value.help


def _target_row(alias, project="target", setting_type=SettingType.PROJECT, user_id="u1"):
    row = _setting(alias, setting_type=setting_type, user_id=user_id, id_=f"tgt-{alias}-{user_id}")
    row.project_name = project
    return row


def _fake_get_by_fields(target_rows):
    """Reproduce Settings.get_by_fields: equality on exactly the supplied keys, first match wins."""

    def _query(fields):
        project = fields.get("project_name.keyword")
        alias = fields.get("alias.keyword")
        user_id = fields.get("user_id.keyword")
        for row in target_rows:
            if row.project_name != project or row.alias != alias:
                continue
            # The PROJECT branch omits user_id entirely, making the query type-blind.
            if "user_id.keyword" in fields and row.user_id != user_id:
                continue
            return row
        return None

    return _query


class TestCollisionMatrix:
    """Exercises the real Settings.check_alias_unique rule (spec section 5.1), unmocked."""

    def _run(self, source_rows, target_rows):
        with patch(
            "codemie.rest_api.models.settings.Settings.get_by_fields", side_effect=_fake_get_by_fields(target_rows)
        ):
            SettingsTransferService._validate_no_collisions(source_rows, "target")

    def test_project_row_collides_with_target_project_row(self):
        # Arrange
        source = [_setting("jira-prod", setting_type=SettingType.PROJECT, user_id="u1")]
        target = [_target_row("jira-prod", setting_type=SettingType.PROJECT, user_id="u9")]

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            self._run(source, target)
        assert excinfo.value.code == status.HTTP_409_CONFLICT

    def test_project_row_collides_with_target_user_row_of_another_owner(self):
        """The PROJECT branch queries (project, alias) only, so it is type- and owner-blind."""
        # Arrange
        source = [_setting("jira-prod", setting_type=SettingType.PROJECT, user_id="u1")]
        target = [_target_row("jira-prod", setting_type=SettingType.USER, user_id="u2")]

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            self._run(source, target)
        assert excinfo.value.code == status.HTTP_409_CONFLICT

    def test_user_row_collides_with_same_owner_target_row(self):
        # Arrange
        source = [_setting("jira-prod", setting_type=SettingType.USER, user_id="u1")]
        target = [_target_row("jira-prod", setting_type=SettingType.USER, user_id="u1")]

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            self._run(source, target)
        assert excinfo.value.code == status.HTTP_409_CONFLICT

    def test_user_row_passes_when_target_alias_belongs_to_another_owner(self):
        """The USER branch queries (project, user_id, alias), so a different owner does not collide."""
        # Arrange
        source = [_setting("jira-prod", setting_type=SettingType.USER, user_id="u2")]
        target = [_target_row("jira-prod", setting_type=SettingType.USER, user_id="u1")]

        # Act / Assert — no exception
        self._run(source, target)

    def test_project_and_other_owner_user_row_both_land_in_empty_target(self):
        # Arrange
        source = [
            _setting("jira-prod", setting_type=SettingType.PROJECT, user_id="u1"),
            _setting("jira-prod", setting_type=SettingType.USER, user_id="u2"),
        ]

        # Act / Assert — no exception
        self._run(source, [])


class _FakeSession:
    """Covers the session surface _apply uses: advisory locks, a FOR UPDATE re-read, writes."""

    def __init__(self, rows_by_id):
        self._rows = dict(rows_by_id)
        self.added = []
        self.commit_calls = 0

    def execute(self, _statement, _params=None):
        """Advisory lock; nothing to simulate."""
        return MagicMock()

    def exec(self, _statement):
        """Stands in for `select(Settings).where(id.in_(...)).with_for_update()`."""
        return SimpleNamespace(all=lambda: list(self._rows.values()))

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commit_calls += 1


class _ExpireOnCommitSession(_FakeSession):
    """Session double that reproduces expire_on_commit=True followed by close().

    A MagicMock session hides CR-001 entirely: real SQLAlchemy expires every tracked instance on
    commit and detaches it on close, so any attribute read afterwards raises DetachedInstanceError.
    This double clears the instances' loaded state on commit so the same class of bug fails here.
    """

    def commit(self):
        super().commit()
        for obj in list(self._rows.values()) + self.added:
            obj.__dict__.clear()


class TestAdvisoryLockId:
    def test_fits_a_postgres_bigint(self):
        # Assert — pg_advisory_xact_lock(bigint) rejects anything wider
        assert -(2**63) <= SettingsTransferService.ADVISORY_LOCK_ID < 2**63

    def test_does_not_collide_with_the_other_advisory_locks(self):
        # Arrange
        from codemie.clients.postgres import _enterprise_migration_lock_id
        from codemie.enterprise.migration import coordinator
        from codemie.utils.leader_lock import LeaderLockContext

        # Assert — one global namespace, so every lock id in the codebase must be distinct
        assert SettingsTransferService.ADVISORY_LOCK_ID not in {
            coordinator._LOCK_ID,
            LeaderLockContext.ADVISORY_LOCK_ID,
            _enterprise_migration_lock_id("mcp_auth"),
        }


class TestLockSourceRows:
    def _session(self, rows):
        session = MagicMock()
        session.exec.side_effect = lambda _stmt: SimpleNamespace(all=lambda: list(rows))
        return session

    def test_returns_rows_in_candidate_order(self):
        # Arrange
        first, second = _setting("a", id_="row-1"), _setting("b", id_="row-2")

        # Act
        rows = SettingsTransferService._lock_source_rows([first, second], "source", self._session([second, first]))

        # Assert
        assert [row.id for row in rows] == ["row-1", "row-2"]

    def test_rejects_row_deleted_mid_transfer(self):
        # Arrange
        candidate = _setting("a", id_="row-1")

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._lock_source_rows([candidate], "source", self._session([]))

        assert excinfo.value.code == status.HTTP_409_CONFLICT
        assert "no longer exists" in excinfo.value.details

    def test_rejects_row_a_concurrent_transfer_already_moved(self):
        # Arrange
        moved = _setting("a", id_="row-1")
        moved.project_name = "somewhere-else"

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._lock_source_rows([moved], "source", self._session([moved]))

        assert excinfo.value.code == status.HTTP_409_CONFLICT
        assert "no longer in project 'source'" in excinfo.value.details


class TestApplyOrdersLockingBeforeValidation:
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_collision_check_runs_under_the_lock_against_freshly_read_rows(self, mock_select, _mp, mock_get_session):
        """The check must not be a separate, unlocked pre-flight."""
        # Arrange
        stale = _setting("jira-prod", id_="row-1")
        fresh = _setting("renamed-by-someone-else", id_="row-1")
        mock_select.return_value = [stale]

        session = MagicMock()
        session.exec.side_effect = lambda _stmt: SimpleNamespace(all=lambda: [fresh])
        mock_get_session.return_value.__enter__.return_value = session

        calls = []
        session.execute.side_effect = lambda stmt, params: calls.append((str(stmt), params)) or MagicMock()

        with patch(
            "codemie.service.settings.settings_transfer_service.Settings.check_alias_unique",
            side_effect=lambda **kwargs: calls.append(kwargs["alias"]) or True,
        ):
            # Act
            SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        # Assert — the lock comes first, then the alias of the re-read row, not the stale one
        lock_sql, lock_params = calls[0]
        assert "pg_advisory_xact_lock" in lock_sql and "try" not in lock_sql
        assert lock_params == {"id": SettingsTransferService.ADVISORY_LOCK_ID}
        assert calls[1:] == ["renamed-by-someone-else"]
        session.commit.assert_called_once()


class TestApplyDoesNotLeakOrmState:
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_no_collisions")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_move_payload_survives_commit_expiry(self, mock_select, _mp, _ma, mock_get_session):
        """CR-001 regression: the response must be materialized before commit expires the rows."""
        # Arrange
        row = _setting("jira-prod", id_="row-1")
        mock_select.return_value = [row]
        session = _ExpireOnCommitSession({"row-1": row})
        mock_get_session.return_value.__enter__.return_value = session

        # Act
        response = SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        # Assert — commit really did expire the instance, yet the payload is intact
        assert session.commit_calls == 1
        assert row.__dict__ == {}
        assert response.transferred_count == 1
        assert response.transferred[0].id == "row-1"
        assert response.transferred[0].alias == "jira-prod"

    @patch("codemie.enterprise.litellm.credentials.clear_litellm_user_credentials_cache")
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_no_collisions")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_copy_payload_and_litellm_flag_survive_commit_expiry(
        self, mock_select, _mp, _ma, mock_get_session, mock_clear
    ):
        # Arrange
        row = _setting("llm-key", CredentialTypes.LITE_LLM, id_="row-1")
        row.credential_values = [CredentialValues(key="api_key", value="test-fake-cipher")]
        mock_select.return_value = [row]
        session = _ExpireOnCommitSession({"row-1": row})
        mock_get_session.return_value.__enter__.return_value = session

        # Act
        response = SettingsTransferService.transfer("source", "target", TransferMode.COPY)

        # Assert
        assert session.commit_calls == 1
        assert response.transferred_count == 1
        assert response.transferred[0].alias == "llm-key"
        assert response.transferred[0].credential_type == CredentialTypes.LITE_LLM
        mock_clear.assert_called_once_with(None)


class TestTransfer:
    def _session_mock(self, rows_by_id):
        session = MagicMock()
        session.exec.side_effect = lambda _stmt: SimpleNamespace(all=lambda: list(rows_by_id.values()))
        return session

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_no_collisions")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_move_updates_project_name_in_place(self, mock_select, _mock_projects, _mock_aliases, mock_get_session):
        # Arrange
        row = _setting("jira-prod", id_="row-1")
        mock_select.return_value = [row]
        session = self._session_mock({"row-1": row})
        mock_get_session.return_value.__enter__.return_value = session

        # Act
        response = SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        # Assert
        assert row.project_name == "target"
        assert row.id == "row-1"
        assert response.transferred_count == 1
        assert response.transferred[0].id == "row-1"
        assert response.skipped_count == 0
        session.commit.assert_called_once()

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_no_collisions")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_copy_creates_new_row_and_leaves_source(self, mock_select, _mp, _ma, mock_get_session):
        # Arrange
        row = _setting("jira-prod", id_="row-1")
        row.created_by = None
        row.credential_values = [CredentialValues(key="token", value="test-fake-cipher")]
        mock_select.return_value = [row]
        session = self._session_mock({"row-1": row})
        mock_get_session.return_value.__enter__.return_value = session

        # Act
        response = SettingsTransferService.transfer("source", "target", TransferMode.COPY)

        # Assert
        assert row.project_name == "source"
        added = session.add.call_args_list[-1].args[0]
        assert added.project_name == "target"
        assert added.id != "row-1"
        assert added.alias == "jira-prod"
        assert added.user_id == row.user_id
        assert added.credential_values[0].value == "test-fake-cipher"
        assert added.credential_values is not row.credential_values
        assert response.transferred[0].id == added.id
        session.commit.assert_called_once()

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_no_collisions")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_copy_preserves_every_clone_field(self, mock_select, _mp, _ma, mock_get_session):
        """Spec section 10: copy preserves created_by, is_global, default and setting_hash verbatim."""
        # Arrange
        row = _setting("jira-prod", id_="row-1")
        row.created_by = CreatedByUser(id="author-9", username="author@example.com", name="Author Nine")
        row.is_global = True
        row.default = True
        row.setting_hash = "test-fake-hash"
        row.credential_values = [CredentialValues(key="token", value="test-fake-cipher")]
        mock_select.return_value = [row]
        session = self._session_mock({"row-1": row})
        mock_get_session.return_value.__enter__.return_value = session

        # Act
        SettingsTransferService.transfer("source", "target", TransferMode.COPY)

        # Assert
        added = session.add.call_args_list[-1].args[0]
        assert added.created_by == row.created_by
        assert added.is_global is True
        assert added.default is True
        assert added.setting_hash == "test-fake-hash"
        assert added.setting_type == row.setting_type

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_collision_mid_set_leaves_nothing_written(self, mock_select, _mp, mock_get_session):
        """Spec section 6: all-or-nothing — a collision aborts before any write."""
        # Arrange
        rows = [_setting("a", id_="row-1"), _setting("b", id_="row-2"), _setting("c", id_="row-3")]
        mock_select.return_value = rows
        session = self._session_mock({row.id: row for row in rows})
        mock_get_session.return_value.__enter__.return_value = session

        with patch(
            "codemie.service.settings.settings_transfer_service.Settings.check_alias_unique",
            side_effect=[True, ValueError("dup"), True],
        ):
            # Act / Assert
            with pytest.raises(ExtendedHTTPException) as excinfo:
                SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        assert excinfo.value.code == status.HTTP_409_CONFLICT
        assert "b" in excinfo.value.details
        # The collision is now detected inside the locked transaction, which rolls back untouched.
        session.commit.assert_not_called()
        session.add.assert_not_called()
        assert all(row.project_name == "source" for row in rows)

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_no_collisions")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_null_alias_trigger_row_is_rejected_before_response_construction(
        self, mock_select, _mp, _ma, mock_get_session
    ):
        """CR-002 regression: an alias-less trigger row must 422, not crash response building."""
        # Arrange
        row = _setting("placeholder", CredentialTypes.WEBHOOK, id_="row-1")
        row.alias = None
        mock_select.return_value = [row]

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService.transfer("source", "target", TransferMode.COPY)

        assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "row-1" in excinfo.value.details
        mock_get_session.assert_not_called()

    @patch("codemie.enterprise.litellm.credentials.clear_litellm_user_credentials_cache")
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_no_collisions")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_clears_litellm_cache_only_when_litellm_transferred(
        self, mock_select, _mp, _ma, mock_get_session, mock_clear
    ):
        # Arrange
        row = _setting("llm-key", CredentialTypes.LITE_LLM, id_="row-1")
        mock_select.return_value = [row]
        mock_get_session.return_value.__enter__.return_value = self._session_mock({"row-1": row})

        # Act
        SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        # Assert
        mock_clear.assert_called_once_with(None)

    @patch("codemie.enterprise.litellm.credentials.clear_litellm_user_credentials_cache")
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_no_collisions")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_no_cache_clear_without_litellm(self, mock_select, _mp, _ma, mock_get_session, mock_clear):
        # Arrange
        row = _setting("jira-prod", id_="row-1")
        mock_select.return_value = [row]
        mock_get_session.return_value.__enter__.return_value = self._session_mock({"row-1": row})

        # Act
        SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        # Assert
        mock_clear.assert_not_called()

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_no_collisions")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_empty_source_returns_zero_counts_without_writing(self, mock_select, _mp, _ma, mock_get_session):
        # Arrange
        mock_select.return_value = []

        # Act
        response = SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        # Assert
        assert response.transferred_count == 0
        assert response.transferred == []
        assert "no transferable integrations" in response.message.lower()
        mock_get_session.assert_not_called()

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_no_collisions")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_copy_reports_skipped_trigger_types(self, mock_select, _mp, _ma, mock_get_session):
        # Arrange
        hook = _setting("gitlab-hook", CredentialTypes.WEBHOOK, id_="row-2")
        mock_select.return_value = [hook]

        # Act
        response = SettingsTransferService.transfer("source", "target", TransferMode.COPY)

        # Assert
        assert response.transferred_count == 0
        assert response.skipped_count == 1
        assert response.skipped[0].id == "row-2"
        assert response.skipped[0].alias == "gitlab-hook"
        mock_get_session.assert_not_called()
