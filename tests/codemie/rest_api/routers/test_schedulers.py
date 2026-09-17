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

"""Tests for EPMCDME-10682 schedulers REST API."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from codemie.repository.scheduler_run_repository import SchedulerRunRepository
from codemie.rest_api.models.scheduler_run import SchedulerRun, SchedulerFilterOptions
from codemie.service.scheduler_run_service import SchedulerRunService


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_run(scheduler_id: str = "sched-1", status: str = "completed") -> SchedulerRun:
    return SchedulerRun(
        id="run-1",
        scheduler_id=scheduler_id,
        status=status,
        trigger="scheduled",
        started_at=datetime(2026, 9, 9, 9, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 9, 9, 0, 18, tzinfo=timezone.utc),
        duration_ms=18000,
    )


# ── Task 2: Repository ────────────────────────────────────────────────────────


def test_repository_get_by_id_returns_run():
    repo = SchedulerRunRepository()
    run = _make_run()
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.get.return_value = run
    with patch("codemie.repository.scheduler_run_repository.Session", return_value=mock_session):
        result = repo.get_by_id("run-1")
    assert result.id == "run-1"
    assert result.status == "completed"


def test_repository_get_by_id_returns_none_for_missing():
    repo = SchedulerRunRepository()
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.get.return_value = None
    with patch("codemie.repository.scheduler_run_repository.Session", return_value=mock_session):
        result = repo.get_by_id("nonexistent")
    assert result is None


# ── Task 3: SchedulerRunService ───────────────────────────────────────────────


def test_service_get_run_raises_not_found_for_missing():
    with patch("codemie.service.scheduler_run_service.SchedulerRunRepository") as mock_repo:
        mock_repo.return_value.get_by_id.return_value = None
        from codemie.core.exceptions import ExtendedHTTPException

        with pytest.raises(ExtendedHTTPException):
            SchedulerRunService().get_run("nonexistent-id")


def test_service_get_stats_returns_stats_model():
    with patch("codemie.service.scheduler_run_service.SchedulerRunRepository") as mock_repo:
        mock_repo.return_value.get_stats.return_value = {
            "total": 10,
            "completed": 8,
            "failed": 2,
            "running": 0,
            "cancelled": 0,
            "successRate": 80.0,
            "averageDurationMs": 5000.0,
        }
        stats = SchedulerRunService().get_stats({})
    assert stats.total == 10
    assert stats.successRate == 80.0


# ── Multi-status filtering ────────────────────────────────────────────────────


def test_repository_list_runs_multi_status_builds_in_clause():
    """list_runs with multiple statuses must use IN(...) not = :status."""
    repo = SchedulerRunRepository()
    captured: dict = {}

    def fake_connect():
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)

        def fake_execute(stmt, *args, **kwargs):
            captured["sql"] = str(stmt)
            result = MagicMock()
            result.scalar_one.return_value = 0
            result.mappings.return_value.all.return_value = []
            return result

        conn.execute = fake_execute
        return conn

    mock_engine = MagicMock()
    mock_engine.connect.return_value = fake_connect()

    with patch.object(repo, "_get_engine", return_value=mock_engine):
        repo.list_runs(status=["completed", "failed"])

    sql = captured.get("sql", "")
    assert "IN (:status_0, :status_1)" in sql
    assert "= :status" not in sql


def test_repository_list_runs_single_status_uses_in_clause():
    """Even a single status value uses the IN(...) path."""
    repo = SchedulerRunRepository()
    captured: dict = {}

    def fake_connect():
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)

        def fake_execute(stmt, *args, **kwargs):
            captured["sql"] = str(stmt)
            result = MagicMock()
            result.scalar_one.return_value = 0
            result.mappings.return_value.all.return_value = []
            return result

        conn.execute = fake_execute
        return conn

    mock_engine = MagicMock()
    mock_engine.connect.return_value = fake_connect()

    with patch.object(repo, "_get_engine", return_value=mock_engine):
        repo.list_runs(status=["completed"])

    sql = captured.get("sql", "")
    assert "IN (:status_0)" in sql


def test_router_parses_comma_separated_status():
    """Comma-separated status query param is split before reaching the service."""
    with patch("codemie.rest_api.routers.schedulers.SchedulerRunService") as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.list_runs.return_value = ([], 0)

        from codemie.rest_api.routers.schedulers import list_scheduler_runs

        list_scheduler_runs(
            scheduler_id=None,
            status="completed,failed",
            resource_type=None,
            resource_id=None,
            date_from=None,
            date_to=None,
            sort_direction="desc",
            page=0,
            per_page=10,
        )

        _, kwargs = mock_svc.list_runs.call_args
        assert kwargs["status"] == ["completed", "failed"]


def test_router_passes_none_status_when_omitted():
    """Omitting status passes None to the service (no filter applied)."""
    with patch("codemie.rest_api.routers.schedulers.SchedulerRunService") as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.list_runs.return_value = ([], 0)

        from codemie.rest_api.routers.schedulers import list_scheduler_runs

        list_scheduler_runs(
            scheduler_id=None,
            status=None,
            resource_type=None,
            resource_id=None,
            date_from=None,
            date_to=None,
            sort_direction="desc",
            page=0,
            per_page=10,
        )

        _, kwargs = mock_svc.list_runs.call_args
        assert kwargs["status"] is None


# ── Task 4: SchedulerSettingsService extensions ───────────────────────────────


def test_patch_is_enabled_calls_flag_modified():
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    mock_cv = MagicMock()
    mock_cv.key = "is_enabled"
    mock_cv.value = True

    mock_setting = MagicMock()
    mock_setting.id = "sched-1"
    mock_setting.credential_values = [mock_cv]
    mock_setting.project_name = "epm-cdme"
    mock_setting.alias = "Daily Report"

    with (
        patch("codemie.service.settings.scheduler_settings_service.Settings") as mock_settings,
        patch("codemie.service.settings.scheduler_settings_service.flag_modified") as mock_flag,
    ):
        mock_settings.get_by_id.return_value = mock_setting
        SchedulerSettingsService.patch_is_enabled("sched-1", False)
        mock_flag.assert_called_once_with(mock_setting, "credential_values")


def test_patch_is_enabled_raises_not_found():
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService
    from codemie.core.exceptions import ExtendedHTTPException

    with patch("codemie.service.settings.scheduler_settings_service.Settings") as mock_settings:
        mock_settings.get_by_id.return_value = None
        with pytest.raises(ExtendedHTTPException):
            SchedulerSettingsService.patch_is_enabled("missing", True)


# ── filter-options ────────────────────────────────────────────────────────────


def test_scheduler_filter_options_model_serializes_correctly():
    """SchedulerFilterOptions round-trips resources and projects lists."""
    from codemie.rest_api.models.scheduler_run import ProjectRef, ResourceRef

    opts = SchedulerFilterOptions(
        resources=[ResourceRef(id="r-1", name="Zebra", type="Assistant")],
        projects=[ProjectRef(id="p-1", name="Alpha Squad")],
    )
    data = opts.model_dump()
    assert data["resources"] == [{"id": "r-1", "name": "Zebra", "type": "Assistant"}]
    assert data["projects"] == [{"id": "p-1", "name": "Alpha Squad"}]


def _make_scheduler_setting(
    resource_id: str,
    resource_name: str,
    resource_type: str,
    project_name: str,
    alias: str = "",
):
    from codemie.rest_api.models.settings import CredentialValues

    cv = [
        CredentialValues(key="resource_id", value=resource_id),
        CredentialValues(key="resource_name", value=resource_name),
        CredentialValues(key="resource_type", value=resource_type),
    ]
    setting = MagicMock()
    setting.id = f"sched-{resource_id}"
    setting.alias = alias or resource_name
    setting.project_name = project_name
    setting.credential_values = cv
    return setting


def test_get_filter_options_deduplicates_and_sorts():
    """get_filter_options() deduplicates by resource_id and project_name, sorted case-insensitively."""
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    settings_rows = [
        _make_scheduler_setting("r-b", "Beta Tool", "Workflow", "gamma-project"),
        _make_scheduler_setting("r-a", "Alpha Tool", "Assistant", "alpha-project"),
        _make_scheduler_setting("r-b", "Beta Tool", "Workflow", "gamma-project"),  # duplicate
        _make_scheduler_setting("r-c", "zebra Tool", "Datasource", "alpha-project"),
    ]

    with patch(
        "codemie.service.settings.scheduler_settings_service.SchedulerSettingsService._fetch_all_scheduler_settings",
        return_value=settings_rows,
    ):
        result = SchedulerSettingsService.get_filter_options()

    resource_ids = [r.id for r in result.resources]
    assert resource_ids == sorted(resource_ids, key=str.lower)
    assert len(resource_ids) == 3
    assert resource_ids.count("r-b") == 1

    project_names = [p.name for p in result.projects]
    assert project_names == sorted(project_names, key=str.lower)
    assert len(project_names) == 2


def test_get_filter_options_skips_empty_resource_name():
    """CR-001: resources[].name must never be empty; entries with a blank name are skipped."""
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    settings_rows = [
        _make_scheduler_setting("r-a", "Alpha Tool", "Assistant", "alpha-project"),
        _make_scheduler_setting("r-blank", "", "Workflow", "alpha-project"),  # no resource_name
    ]

    with patch(
        "codemie.service.settings.scheduler_settings_service.SchedulerSettingsService._fetch_all_scheduler_settings",
        return_value=settings_rows,
    ):
        result = SchedulerSettingsService.get_filter_options()

    resource_ids = [r.id for r in result.resources]
    assert "r-blank" not in resource_ids
    assert "r-a" in resource_ids
    assert all(r.name for r in result.resources)


def test_get_filter_options_router_endpoint():
    """GET /v1/schedulers/filter-options returns 200 with resources and projects."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from codemie.rest_api.routers.schedulers import router
    from codemie.rest_api.models.scheduler_run import ProjectRef, ResourceRef
    import codemie.rest_api.security.authentication as auth_module

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[auth_module.authenticate] = lambda: None

    mock_result = SchedulerFilterOptions(
        resources=[ResourceRef(id="r-1", name="Alpha", type="Assistant")],
        projects=[ProjectRef(id="p-1", name="Team A")],
    )

    with patch(
        "codemie.service.settings.scheduler_settings_service.SchedulerSettingsService.get_filter_options",
        return_value=mock_result,
    ):
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/v1/schedulers/filter-options")

    assert response.status_code == 200
    body = response.json()
    assert "resources" in body
    assert "projects" in body
    assert body["resources"][0]["id"] == "r-1"
    assert body["projects"][0]["id"] == "p-1"


# ── T4: Router DELETE /scheduler-runs/{run_id} ────────────────────────────────


def _make_delete_client():
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    import codemie.rest_api.security.authentication as auth_module
    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.rest_api.routers.schedulers import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[auth_module.authenticate] = lambda: None

    @app.exception_handler(ExtendedHTTPException)
    async def _handle(request: Request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"error": exc.message})

    return TestClient(app, raise_server_exceptions=False)


def test_delete_scheduler_run_returns_204():
    client = _make_delete_client()
    with patch("codemie.rest_api.routers.schedulers.SchedulerRunService") as mock_cls:
        mock_svc = MagicMock()
        mock_cls.return_value = mock_svc
        response = client.delete("/v1/scheduler-runs/run-1")
    assert response.status_code == 204
    mock_svc.delete_run.assert_called_once_with("run-1")


def test_delete_scheduler_run_404_propagates():
    from codemie.core.exceptions import ExtendedHTTPException

    client = _make_delete_client()
    with patch("codemie.rest_api.routers.schedulers.SchedulerRunService") as mock_cls:
        mock_svc = MagicMock()
        mock_cls.return_value = mock_svc
        mock_svc.delete_run.side_effect = ExtendedHTTPException(code=404, message="not found")
        response = client.delete("/v1/scheduler-runs/missing")
    assert response.status_code == 404


def test_delete_scheduler_run_409_propagates():
    from codemie.core.exceptions import ExtendedHTTPException

    client = _make_delete_client()
    with patch("codemie.rest_api.routers.schedulers.SchedulerRunService") as mock_cls:
        mock_svc = MagicMock()
        mock_cls.return_value = mock_svc
        mock_svc.delete_run.side_effect = ExtendedHTTPException(code=409, message="conflict")
        response = client.delete("/v1/scheduler-runs/run-1")
    assert response.status_code == 409


# ── T3: Service delete_run ────────────────────────────────────────────────────


def test_service_delete_run_raises_404_when_not_found():
    from codemie.core.exceptions import ExtendedHTTPException

    svc = SchedulerRunService()
    with patch.object(svc._repo, "delete_run", return_value="not_found"):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            svc.delete_run("missing-id")
    assert exc_info.value.code == 404


def test_service_delete_run_raises_409_when_running():
    from codemie.core.exceptions import ExtendedHTTPException

    svc = SchedulerRunService()
    with patch.object(svc._repo, "delete_run", return_value="running"):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            svc.delete_run("run-1")
    assert exc_info.value.code == 409


def test_service_delete_run_succeeds():
    svc = SchedulerRunService()
    run = _make_run(status="completed")
    with patch.object(svc._repo, "get_by_id", return_value=run):
        with patch.object(svc._repo, "delete_run") as mock_delete:
            svc.delete_run("run-1")
    mock_delete.assert_called_once_with("run-1")


# ── T2: Repository delete_run ─────────────────────────────────────────────────


def test_repository_delete_run_removes_record():
    repo = SchedulerRunRepository()

    mock_delete_result = MagicMock()
    mock_delete_result.rowcount = 1

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value = mock_delete_result

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_conn

    with patch.object(repo, "_get_engine", return_value=mock_engine):
        result = repo.delete_run("run-1")

    mock_conn.commit.assert_called_once()
    assert result is None


def test_repository_delete_run_noop_when_missing():
    repo = SchedulerRunRepository()
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.get.return_value = None
    with patch("codemie.repository.scheduler_run_repository.Session", return_value=mock_session):
        repo.delete_run("missing-id")
    mock_session.delete.assert_not_called()


# ── T1: raise_conflict ────────────────────────────────────────────────────────


# ── EPMCDME-14805: ownerType filter ──────────────────────────────────────────


def test_router_forwards_owner_type_to_service():
    """ownerType query param is forwarded as owner_type to SchedulerSettingsService."""
    with patch("codemie.rest_api.routers.schedulers.SchedulerSettingsService") as mock_svc_cls:
        mock_svc_cls.list_schedulers.return_value = MagicMock(items=[], total=0, page=0, pageSize=10, totalPages=0)

        from codemie.rest_api.routers.schedulers import list_schedulers

        list_schedulers(
            page=0,
            page_size=10,
            search=None,
            resource_type=None,
            project_id=None,
            resource_id=None,
            status=None,
            last_run_status=None,
            owner_type="Project",
        )

        _, kwargs = mock_svc_cls.list_schedulers.call_args
        assert kwargs["owner_type"] == "Project"


def test_build_list_filters_appends_setting_type_when_owner_type_provided():
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type="User",
    )

    assert "s.setting_type = :owner_type" in conditions
    assert params["owner_type"] == "USER"


def test_build_list_filters_omits_setting_type_when_owner_type_is_none():
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type=None,
    )

    assert "s.setting_type = :owner_type" not in conditions
    assert "owner_type" not in params


def test_raise_conflict_raises_409():
    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.rest_api.routers.utils import raise_conflict

    with pytest.raises(ExtendedHTTPException) as exc_info:
        raise_conflict("Cannot delete a running run")
    assert exc_info.value.code == 409
