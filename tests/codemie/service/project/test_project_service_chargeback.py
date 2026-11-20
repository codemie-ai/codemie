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

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.project.project_service import ProjectService


def _make_admin_user() -> MagicMock:
    user = MagicMock(id="admin-1", is_admin=True)
    user.email = "admin-1@example.com"
    user.name = "Admin One"
    return user


class TestProjectServiceChargebackEnabled:
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_passes_chargeback_enabled_to_repository(
        self, mock_get_session, mock_app_repo, mock_customer_config
    ):
        mock_session = MagicMock()

        @contextmanager
        def _ctx():
            yield mock_session

        mock_get_session.return_value = _ctx()
        mock_customer_config.is_feature_enabled.return_value = True

        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="desc",
            cost_center_id=None,
            chargeback_enabled=False,
            chargeback_attribution="project",
        )
        mock_app_repo.update_project.return_value = mock_project

        with (
            patch(
                "codemie.service.project.project_service.ProjectService._get_project_for_update",
                return_value=mock_project,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_project_description",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_cost_center_id",
                return_value=None,
            ),
            patch("codemie.service.project.project_service.SettingsService"),
        ):
            ProjectService.update_project(
                user=_make_admin_user(),
                project_name="my-project",
                chargeback_enabled=True,
            )

        call_kwargs = mock_app_repo.update_project.call_args.kwargs
        assert call_kwargs.get("chargeback_enabled") is True


class TestProjectServiceChargebackAttribution:
    @staticmethod
    @contextmanager
    def _session_ctx(mock_session):
        yield mock_session

    def _run_update_project(self, mock_get_session, mock_app_repo, **kwargs):
        mock_session = MagicMock()
        mock_get_session.return_value = self._session_ctx(mock_session)

        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="desc",
            cost_center_id=None,
            chargeback_enabled=False,
            chargeback_attribution="project",
        )
        mock_app_repo.update_project.return_value = mock_project

        with (
            patch(
                "codemie.service.project.project_service.ProjectService._get_project_for_update",
                return_value=mock_project,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_project_description",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_cost_center_id",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_chargeback_attribution",
                return_value=None,
            ),
            patch("codemie.service.project.project_service.customer_config", create=True) as mock_customer_config,
            patch("codemie.service.project.project_service.SettingsService"),
        ):
            mock_customer_config.is_feature_enabled.return_value = True
            ProjectService.update_project(
                user=_make_admin_user(),
                project_name="my-project",
                **kwargs,
            )

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_passes_chargeback_attribution_to_repository(self, mock_get_session, mock_app_repo):
        self._run_update_project(mock_get_session, mock_app_repo, chargeback_attribution="cost_center")

        call_kwargs = mock_app_repo.update_project.call_args.kwargs
        assert call_kwargs.get("chargeback_attribution") == "cost_center"

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_omits_chargeback_attribution_as_noop(self, mock_get_session, mock_app_repo):
        self._run_update_project(mock_get_session, mock_app_repo)

        call_kwargs = mock_app_repo.update_project.call_args.kwargs
        assert call_kwargs.get("chargeback_attribution") is None


class TestProjectServiceChargebackFeatureGate:
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_rejects_chargeback_when_flag_disabled(
        self, mock_get_session, mock_app_repo, mock_customer_config
    ):
        mock_session = MagicMock()

        @contextmanager
        def _ctx():
            yield mock_session

        mock_get_session.return_value = _ctx()
        mock_customer_config.is_feature_enabled.return_value = False

        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="desc",
            cost_center_id=None,
            chargeback_enabled=False,
            chargeback_attribution="project",
        )
        mock_app_repo.update_project.return_value = mock_project

        with (
            patch(
                "codemie.service.project.project_service.ProjectService._get_project_for_update",
                return_value=mock_project,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_project_description",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_cost_center_id",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_chargeback_attribution",
                return_value=None,
            ),
            patch("codemie.service.project.project_service.SettingsService"),
            pytest.raises(ExtendedHTTPException) as exc_info,
        ):
            ProjectService.update_project(
                user=_make_admin_user(),
                project_name="my-project",
                chargeback_attribution="project",
            )

        assert exc_info.value.code == 403
        mock_app_repo.update_project.assert_not_called()


class TestProjectServiceChargebackAttributionValidation:
    def _run_update_project(
        self,
        mock_get_session,
        mock_app_repo,
        mock_customer_config,
        mock_cost_center_repo,
        *,
        chargeback_attribution,
        resolved_cost_center_id,
        active_cost_center,
    ):
        mock_session = MagicMock()

        @contextmanager
        def _ctx():
            yield mock_session

        mock_get_session.return_value = _ctx()
        mock_customer_config.is_feature_enabled.return_value = True
        mock_cost_center_repo.get_active_by_id.return_value = active_cost_center

        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="desc",
            cost_center_id=None,
            chargeback_enabled=False,
            chargeback_attribution="project",
        )
        mock_app_repo.update_project.return_value = mock_project

        with (
            patch(
                "codemie.service.project.project_service.ProjectService._get_project_for_update",
                return_value=mock_project,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_project_description",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_cost_center_id",
                return_value=resolved_cost_center_id,
            ),
            patch("codemie.service.project.project_service.SettingsService"),
        ):
            ProjectService.update_project(
                user=_make_admin_user(),
                project_name="my-project",
                chargeback_attribution=chargeback_attribution,
            )

    @patch("codemie.service.project.project_service.cost_center_repository", create=True)
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_project_attribution_never_checks_cost_center(
        self, mock_get_session, mock_app_repo, mock_customer_config, mock_cost_center_repo
    ):
        self._run_update_project(
            mock_get_session,
            mock_app_repo,
            mock_customer_config,
            mock_cost_center_repo,
            chargeback_attribution="project",
            resolved_cost_center_id=None,
            active_cost_center=None,
        )

        mock_cost_center_repo.get_active_by_id.assert_not_called()
        mock_app_repo.update_project.assert_called_once()

    @patch("codemie.service.project.project_service.cost_center_repository", create=True)
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_cost_center_attribution_succeeds_with_active_cost_center(
        self, mock_get_session, mock_app_repo, mock_customer_config, mock_cost_center_repo
    ):
        cost_center_id = uuid4()
        self._run_update_project(
            mock_get_session,
            mock_app_repo,
            mock_customer_config,
            mock_cost_center_repo,
            chargeback_attribution="cost_center",
            resolved_cost_center_id=cost_center_id,
            active_cost_center=MagicMock(),
        )

        mock_app_repo.update_project.assert_called_once()

    @patch("codemie.service.project.project_service.cost_center_repository", create=True)
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_cost_center_attribution_without_linked_cost_center_raises_400(
        self, mock_get_session, mock_app_repo, mock_customer_config, mock_cost_center_repo
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._run_update_project(
                mock_get_session,
                mock_app_repo,
                mock_customer_config,
                mock_cost_center_repo,
                chargeback_attribution="cost_center",
                resolved_cost_center_id=None,
                active_cost_center=None,
            )

        assert exc_info.value.code == 400
        mock_app_repo.update_project.assert_not_called()

    @patch("codemie.service.project.project_service.cost_center_repository", create=True)
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_cost_center_attribution_with_soft_deleted_cost_center_raises_400(
        self, mock_get_session, mock_app_repo, mock_customer_config, mock_cost_center_repo
    ):
        cost_center_id = uuid4()
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._run_update_project(
                mock_get_session,
                mock_app_repo,
                mock_customer_config,
                mock_cost_center_repo,
                chargeback_attribution="cost_center",
                resolved_cost_center_id=cost_center_id,
                active_cost_center=None,
            )

        assert exc_info.value.code == 400
        mock_app_repo.update_project.assert_not_called()

    @patch("codemie.service.project.project_service.cost_center_repository", create=True)
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_unrecognized_attribution_value_raises_400_before_repository(
        self, mock_get_session, mock_app_repo, mock_customer_config, mock_cost_center_repo
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._run_update_project(
                mock_get_session,
                mock_app_repo,
                mock_customer_config,
                mock_cost_center_repo,
                chargeback_attribution="not_a_real_value",
                resolved_cost_center_id=None,
                active_cost_center=None,
            )

        assert exc_info.value.code == 400
        mock_cost_center_repo.get_active_by_id.assert_not_called()
        mock_app_repo.update_project.assert_not_called()


class TestProjectServiceChargebackAttributionUnrelatedUpdate:
    """A PATCH that touches neither chargeback_attribution nor cost_center_id must not
    re-validate the project's already-stored attribution against its linked cost center."""

    @patch("codemie.service.project.project_service.cost_center_repository", create=True)
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_unrelated_update_skips_validation_even_with_soft_deleted_cost_center(
        self, mock_get_session, mock_app_repo, mock_customer_config, mock_cost_center_repo
    ):
        mock_session = MagicMock()

        @contextmanager
        def _ctx():
            yield mock_session

        mock_get_session.return_value = _ctx()
        mock_customer_config.is_feature_enabled.return_value = True
        mock_cost_center_repo.get_active_by_id.return_value = None  # cost center soft-deleted

        stored_cost_center_id = uuid4()
        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="old desc",
            cost_center_id=stored_cost_center_id,
            chargeback_enabled=True,
            chargeback_attribution="cost_center",
        )
        mock_app_repo.update_project.return_value = mock_project

        with (
            patch(
                "codemie.service.project.project_service.ProjectService._get_project_for_update",
                return_value=mock_project,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_project_description",
                return_value="new desc",
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
                return_value=None,
            ),
            patch("codemie.service.project.project_service.SettingsService"),
        ):
            ProjectService.update_project(
                user=_make_admin_user(),
                project_name="my-project",
                description="new desc",
            )

        mock_cost_center_repo.get_active_by_id.assert_not_called()
        mock_app_repo.update_project.assert_called_once()
        call_kwargs = mock_app_repo.update_project.call_args.kwargs
        assert call_kwargs.get("cost_center_id") == stored_cost_center_id
        assert call_kwargs.get("chargeback_attribution") is None


class TestProjectServiceCostCentersFeatureGate:
    """Covers the costCenters flag gate on chargeback_attribution='cost_center'."""

    @patch("codemie.service.project.project_service.cost_center_repository", create=True)
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_cost_center_attribution_rejected_when_cost_centers_flag_disabled(
        self, mock_get_session, mock_app_repo, mock_customer_config, mock_cost_center_repo
    ):
        mock_session = MagicMock()

        @contextmanager
        def _ctx():
            yield mock_session

        mock_get_session.return_value = _ctx()
        mock_customer_config.is_feature_enabled.side_effect = lambda flag: flag == "projectChargeback"

        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="desc",
            cost_center_id=None,
            chargeback_enabled=False,
            chargeback_attribution="project",
        )
        mock_app_repo.update_project.return_value = mock_project

        with (
            patch(
                "codemie.service.project.project_service.ProjectService._get_project_for_update",
                return_value=mock_project,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_project_description",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_cost_center_id",
                return_value=None,
            ),
            patch("codemie.service.project.project_service.SettingsService"),
            pytest.raises(ExtendedHTTPException) as exc_info,
        ):
            ProjectService.update_project(
                user=_make_admin_user(),
                project_name="my-project",
                chargeback_attribution="cost_center",
            )

        assert exc_info.value.code == 403
        mock_cost_center_repo.get_active_by_id.assert_not_called()
        mock_app_repo.update_project.assert_not_called()

    @patch("codemie.service.project.project_service.cost_center_repository", create=True)
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_project_attribution_unaffected_by_cost_centers_flag_disabled(
        self, mock_get_session, mock_app_repo, mock_customer_config, mock_cost_center_repo
    ):
        mock_session = MagicMock()

        @contextmanager
        def _ctx():
            yield mock_session

        mock_get_session.return_value = _ctx()
        mock_customer_config.is_feature_enabled.side_effect = lambda flag: flag == "projectChargeback"

        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="desc",
            cost_center_id=None,
            chargeback_enabled=False,
            chargeback_attribution="project",
        )
        mock_app_repo.update_project.return_value = mock_project

        with (
            patch(
                "codemie.service.project.project_service.ProjectService._get_project_for_update",
                return_value=mock_project,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_project_description",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_cost_center_id",
                return_value=None,
            ),
            patch("codemie.service.project.project_service.SettingsService"),
        ):
            ProjectService.update_project(
                user=_make_admin_user(),
                project_name="my-project",
                chargeback_attribution="project",
            )

        mock_cost_center_repo.get_active_by_id.assert_not_called()
        mock_app_repo.update_project.assert_called_once()


class TestProjectServiceChargebackClearCostCenter:
    """Covers CR-001: clearing cost_center_id must not orphan a persisted 'cost_center' attribution."""

    @patch("codemie.service.project.project_service.cost_center_repository", create=True)
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_clearing_cost_center_with_stale_cost_center_attribution_raises_400(
        self, mock_get_session, mock_app_repo, mock_customer_config, mock_cost_center_repo
    ):
        mock_session = MagicMock()

        @contextmanager
        def _ctx():
            yield mock_session

        mock_get_session.return_value = _ctx()
        mock_customer_config.is_feature_enabled.return_value = True
        mock_cost_center_repo.get_active_by_id.return_value = None

        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="desc",
            cost_center_id=uuid4(),
            chargeback_enabled=True,
            chargeback_attribution="cost_center",
        )
        mock_app_repo.update_project.return_value = mock_project

        with (
            patch(
                "codemie.service.project.project_service.ProjectService._get_project_for_update",
                return_value=mock_project,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_project_description",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
                return_value=None,
            ),
            patch("codemie.service.project.project_service.SettingsService"),
            pytest.raises(ExtendedHTTPException) as exc_info,
        ):
            ProjectService.update_project(
                user=_make_admin_user(),
                project_name="my-project",
                clear_cost_center=True,
            )

        assert exc_info.value.code == 400
        mock_app_repo.update_project.assert_not_called()

    @patch("codemie.service.project.project_service.cost_center_repository", create=True)
    @patch("codemie.service.project.project_service.customer_config", create=True)
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_clearing_cost_center_together_with_project_attribution_succeeds(
        self, mock_get_session, mock_app_repo, mock_customer_config, mock_cost_center_repo
    ):
        mock_session = MagicMock()

        @contextmanager
        def _ctx():
            yield mock_session

        mock_get_session.return_value = _ctx()
        mock_customer_config.is_feature_enabled.return_value = True

        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="desc",
            cost_center_id=uuid4(),
            chargeback_enabled=True,
            chargeback_attribution="cost_center",
        )
        mock_app_repo.update_project.return_value = mock_project

        with (
            patch(
                "codemie.service.project.project_service.ProjectService._get_project_for_update",
                return_value=mock_project,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_project_description",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
                return_value=None,
            ),
            patch("codemie.service.project.project_service.SettingsService"),
        ):
            ProjectService.update_project(
                user=_make_admin_user(),
                project_name="my-project",
                clear_cost_center=True,
                chargeback_attribution="project",
            )

        mock_cost_center_repo.get_active_by_id.assert_not_called()
        call_kwargs = mock_app_repo.update_project.call_args.kwargs
        assert call_kwargs.get("cost_center_id") is None
        assert call_kwargs.get("chargeback_attribution") == "project"
