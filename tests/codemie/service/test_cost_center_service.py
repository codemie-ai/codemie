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

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.cost_center_service import CostCenterService


def _admin_user():
    user = MagicMock()
    user.is_admin = True
    user.is_maintainer = False
    user.is_auditor = False
    user.is_admin_or_maintainer = True
    user.id = "actor-1"
    return user


def _cost_center(name="eng-123"):
    cc = MagicMock()
    cc.id = uuid4()
    cc.name = name
    return cc


class TestCostCenterService:
    @pytest.mark.parametrize(
        "name",
        [
            "eng-123",
            "aa-bb2",
            "1a-2b",
        ],
    )
    def test_validate_name_accepts_single_separator_pattern(self, name: str):
        assert CostCenterService.validate_name(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "eng--123",
            "eng-123-extra",
            "eng_123",
            "-eng123",
            "eng123-",
        ],
    )
    def test_validate_name_rejects_invalid_separator_pattern(self, name: str):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.validate_name(name)

        assert exc_info.value.code == 400
        assert exc_info.value.message == "Invalid cost center name"


class TestCostCenterServiceLogging:
    @patch("codemie.service.cost_center_service.logger")
    @patch("codemie.service.cost_center_service.cost_center_repository")
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_create_logs_on_success(self, mock_customer_config, mock_repo, mock_logger):
        """create emits a logger.info containing 'cost_center_created'."""
        mock_customer_config.is_feature_enabled.return_value = True
        mock_repo.get_by_name_case_insensitive.return_value = None
        mock_repo.create.return_value = _cost_center()

        CostCenterService.create(MagicMock(), user=_admin_user(), name="eng-123", description="desc")

        mock_logger.info.assert_called_once()
        assert "cost_center_created" in mock_logger.info.call_args[0][0]

    @patch("codemie.service.cost_center_service.logger")
    @patch("codemie.service.cost_center_service.cost_center_repository")
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_update_logs_on_success(self, mock_customer_config, mock_repo, mock_logger):
        """update emits a logger.info containing 'cost_center_updated'."""
        mock_customer_config.is_feature_enabled.return_value = True
        existing = _cost_center()
        mock_repo.get_active_by_id.return_value = existing
        mock_repo.update.return_value = existing

        CostCenterService.update(MagicMock(), user=_admin_user(), cost_center_id=existing.id, description="new")

        mock_logger.info.assert_called_once()
        assert "cost_center_updated" in mock_logger.info.call_args[0][0]

    @patch("codemie.service.cost_center_service.logger")
    @patch("codemie.service.cost_center_service.application_repository")
    @patch("codemie.service.cost_center_service.cost_center_repository")
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_delete_logs_on_success(self, mock_customer_config, mock_repo, mock_app_repo, mock_logger):
        """delete emits a logger.info containing 'cost_center_deleted'."""
        mock_customer_config.is_feature_enabled.return_value = True
        existing = _cost_center()
        mock_repo.get_active_by_id.return_value = existing
        mock_app_repo.count_active_projects_by_cost_center_id.return_value = 0

        CostCenterService.delete(MagicMock(), user=_admin_user(), cost_center_id=existing.id)

        mock_logger.info.assert_called_once()
        assert "cost_center_deleted" in mock_logger.info.call_args[0][0]

    @patch("codemie.service.cost_center_service.logger")
    @patch("codemie.service.cost_center_service.application_repository")
    @patch("codemie.service.cost_center_service.cost_center_repository")
    def test_delete_does_not_log_when_projects_linked(self, mock_repo, mock_app_repo, mock_logger):
        """delete must not log when it aborts on linked active projects."""
        existing = _cost_center()
        mock_repo.get_active_by_id.return_value = existing
        mock_app_repo.count_active_projects_by_cost_center_id.return_value = 3

        with pytest.raises(ExtendedHTTPException):
            CostCenterService.delete(MagicMock(), user=_admin_user(), cost_center_id=existing.id)

        mock_logger.info.assert_not_called()


class TestCostCenterServiceFeatureGate:
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_create_rejects_when_feature_disabled(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.create(MagicMock(), user=_admin_user(), name="eng-123", description=None)

        assert exc_info.value.code == 403
        mock_customer_config.is_feature_enabled.assert_called_once_with("costCenters")

    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_list_paginated_rejects_when_feature_disabled(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.list_paginated(MagicMock(), user=_admin_user(), search=None, page=1, per_page=10)

        assert exc_info.value.code == 403
        assert exc_info.value.message == "Feature not available"

    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_get_for_read_or_404_rejects_when_feature_disabled(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.get_for_read_or_404(MagicMock(), user=_admin_user(), cost_center_id=uuid4())

        assert exc_info.value.code == 403
        assert exc_info.value.message == "Feature not available"

    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_update_rejects_when_feature_disabled(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.update(MagicMock(), user=_admin_user(), cost_center_id=uuid4(), description="x")

        assert exc_info.value.code == 403

    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_delete_rejects_when_feature_disabled(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.delete(MagicMock(), user=_admin_user(), cost_center_id=uuid4())

        assert exc_info.value.code == 403

    @patch("codemie.service.cost_center_service.cost_center_repository")
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_ensure_exists_for_project_rejects_when_feature_disabled(self, mock_customer_config, mock_repo):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.ensure_exists_for_project(MagicMock(), uuid4())

        assert exc_info.value.code == 403
        mock_repo.get_active_by_id.assert_not_called()

    @patch("codemie.service.cost_center_service.cost_center_repository")
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_ensure_exists_for_project_skips_feature_check_when_cost_center_id_is_none(
        self, mock_customer_config, mock_repo
    ):
        mock_customer_config.is_feature_enabled.return_value = False

        result = CostCenterService.ensure_exists_for_project(MagicMock(), None)

        assert result is None
        mock_customer_config.is_feature_enabled.assert_not_called()
        mock_repo.get_active_by_id.assert_not_called()

    @patch("codemie.service.cost_center_service.cost_center_repository")
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_ensure_exists_for_project_succeeds_when_feature_enabled(self, mock_customer_config, mock_repo):
        mock_customer_config.is_feature_enabled.return_value = True
        cost_center = _cost_center()
        mock_repo.get_active_by_id.return_value = cost_center

        result = CostCenterService.ensure_exists_for_project(MagicMock(), cost_center.id)

        assert result is cost_center


@patch("codemie.service.cost_center_service.customer_config", create=True)
@patch("codemie.service.cost_center_service.cost_center_repository")
class TestGetOrCreate:
    def test_returns_existing_active_cost_center(self, mock_repo, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = True
        existing = _cost_center("nbs-cmpa")
        existing.deleted_at = None
        mock_repo.get_by_name_case_insensitive.return_value = existing
        session = MagicMock()

        result = CostCenterService.get_or_create(session, name="nbs-cmpa", created_by="sa-1")

        assert result == (existing, False)
        mock_repo.create.assert_not_called()

    def test_creates_when_missing(self, mock_repo, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = True
        mock_repo.get_by_name_case_insensitive.return_value = None
        created = _cost_center("nbs-cmpa")
        mock_repo.create.return_value = created
        session = MagicMock()

        cost_center, was_created = CostCenterService.get_or_create(session, name="nbs-cmpa", created_by="sa-1")

        assert cost_center is created
        assert was_created is True
        mock_repo.create.assert_called_once_with(session, name="nbs-cmpa", description=None, created_by="sa-1")

    def test_logs_cost_center_created_when_creating(self, mock_repo, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = True
        mock_repo.get_by_name_case_insensitive.return_value = None
        mock_repo.create.return_value = _cost_center("nbs-cmpa")

        with patch("codemie.service.cost_center_service.logger") as mock_logger:
            CostCenterService.get_or_create(MagicMock(), name="nbs-cmpa", created_by="sa-1")

        mock_logger.info.assert_called_once()
        message = mock_logger.info.call_args[0][0]
        assert "cost_center_created" in message
        assert "by=sa-1" in message

    def test_soft_deleted_match_raises_409(self, mock_repo, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = True
        match = _cost_center("nbs-cmpa")
        match.deleted_at = datetime(2026, 1, 1)
        mock_repo.get_by_name_case_insensitive.return_value = match

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.get_or_create(MagicMock(), name="nbs-cmpa", created_by="sa-1")

        assert exc_info.value.code == 409
        assert exc_info.value.message == "Cost center 'nbs-cmpa' has been deleted"
        assert exc_info.value.help == "Restore the cost center or use a different cost center name."
        mock_repo.create.assert_not_called()

    def test_feature_disabled_raises_403(self, mock_repo, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.get_or_create(MagicMock(), name="nbs-cmpa", created_by="sa-1")

        assert exc_info.value.code == 403
        mock_repo.get_by_name_case_insensitive.assert_not_called()
        mock_repo.create.assert_not_called()

    def test_invalid_name_raises_400(self, mock_repo, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = True

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.get_or_create(MagicMock(), name="nbs--cmpa", created_by="sa-1")

        assert exc_info.value.code == 400
        mock_repo.get_by_name_case_insensitive.assert_not_called()
        mock_repo.create.assert_not_called()

    def test_does_not_require_admin(self, mock_repo, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = True
        mock_repo.get_by_name_case_insensitive.return_value = None
        mock_repo.create.return_value = _cost_center("nbs-cmpa")

        with patch.object(CostCenterService, "ensure_admin") as mock_ensure_admin:
            CostCenterService.get_or_create(MagicMock(), name="nbs-cmpa", created_by="sa-1")

        mock_ensure_admin.assert_not_called()

    def test_integrity_error_propagates(self, mock_repo, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = True
        mock_repo.get_by_name_case_insensitive.return_value = None
        mock_repo.create.side_effect = IntegrityError("insert", {}, Exception("duplicate"))

        with pytest.raises(IntegrityError):
            CostCenterService.get_or_create(MagicMock(), name="nbs-cmpa", created_by="sa-1")


def _auditor_user():
    user = MagicMock()
    user.is_admin_or_maintainer = False
    user.is_auditor = True
    user.id = "auditor-1"
    return user


def _plain_user():
    user = MagicMock()
    user.is_admin_or_maintainer = False
    user.is_auditor = False
    user.id = "user-1"
    return user


@patch("codemie.service.cost_center_service.application_repository")
@patch("codemie.service.cost_center_service.cost_center_repository")
@patch("codemie.service.cost_center_service.customer_config", create=True)
class TestCostCenterServiceAuthorization:
    """EPMCDME-17380: auditors read cost centers; every write stays admin/maintainer-only."""

    def test_auditor_can_list(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = True
        mock_repo.list_paginated.return_value = ([], 0)
        session = MagicMock()

        result = CostCenterService.list_paginated(session, user=_auditor_user(), search=None, page=0, per_page=20)

        assert result == ([], 0)
        mock_repo.list_paginated.assert_called_once_with(session, search=None, page=0, per_page=20)

    def test_auditor_can_get(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = True
        existing = _cost_center()
        mock_repo.get_active_by_id.return_value = existing

        result = CostCenterService.get_for_read_or_404(MagicMock(), user=_auditor_user(), cost_center_id=existing.id)

        assert result is existing

    def test_plain_user_cannot_list(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = True

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.list_paginated(MagicMock(), user=_plain_user(), search=None, page=0, per_page=20)

        assert exc_info.value.code == 403
        assert exc_info.value.message == "Access denied"
        mock_repo.list_paginated.assert_not_called()

    def test_plain_user_cannot_get(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = True

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.get_for_read_or_404(MagicMock(), user=_plain_user(), cost_center_id=uuid4())

        assert exc_info.value.code == 403
        assert exc_info.value.message == "Access denied"
        mock_repo.get_active_by_id.assert_not_called()

    def test_feature_gate_runs_before_read_role_check(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.get_for_read_or_404(MagicMock(), user=_plain_user(), cost_center_id=uuid4())

        assert exc_info.value.message == "Feature not available"

    def test_list_feature_gate_runs_before_read_role_check(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.list_paginated(MagicMock(), user=_plain_user(), search=None, page=1, per_page=10)

        assert exc_info.value.message == "Feature not available"
        mock_repo.list_paginated.assert_not_called()

    def test_admin_fixture_passes_read_check(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = True

        CostCenterService.ensure_read_access(_admin_user())

    def test_auditor_cannot_create(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = True
        session = MagicMock()

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.create(session, user=_auditor_user(), name="eng-123", description=None)

        assert exc_info.value.code == 403
        mock_repo.create.assert_not_called()
        session.flush.assert_not_called()

    def test_auditor_cannot_update(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = True
        existing = _cost_center()
        mock_repo.get_active_by_id.return_value = existing
        session = MagicMock()

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.update(session, user=_auditor_user(), cost_center_id=existing.id, description="new")

        assert exc_info.value.code == 403
        assert exc_info.value.message == "Access denied"
        mock_repo.update.assert_not_called()
        session.flush.assert_not_called()

    def test_auditor_cannot_delete(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = True
        existing = _cost_center()
        existing.deleted_at = None
        mock_repo.get_active_by_id.return_value = existing
        mock_app_repo.count_active_projects_by_cost_center_id.return_value = 0
        session = MagicMock()

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.delete(session, user=_auditor_user(), cost_center_id=existing.id)

        assert exc_info.value.code == 403
        assert exc_info.value.message == "Access denied"
        assert existing.deleted_at is None
        session.add.assert_not_called()
        session.flush.assert_not_called()

    def test_admin_can_update_and_delete(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = True
        existing = _cost_center()
        mock_repo.get_active_by_id.return_value = existing
        mock_repo.update.return_value = existing
        mock_app_repo.count_active_projects_by_cost_center_id.return_value = 0
        session = MagicMock()

        updated = CostCenterService.update(session, user=_admin_user(), cost_center_id=existing.id, description="new")
        CostCenterService.delete(session, user=_admin_user(), cost_center_id=existing.id)

        assert updated is existing
        mock_repo.update.assert_called_once_with(session, existing, description="new")
        assert existing.deleted_at is not None
        session.flush.assert_called_once()

    def test_update_returns_404_for_missing_cost_center(self, mock_customer_config, mock_repo, mock_app_repo):
        mock_customer_config.is_feature_enabled.return_value = True
        mock_repo.get_active_by_id.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.update(MagicMock(), user=_admin_user(), cost_center_id=uuid4(), description="x")

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Cost center not found"
