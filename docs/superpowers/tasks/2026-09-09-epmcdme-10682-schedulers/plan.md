# EPMCDME-10682: Schedulers REST API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose a Schedulers REST API (list, toggle, run history, stats, run detail, paginated logs) backed by a new `scheduler_runs` table and APScheduler persistence.

**Architecture:** Scheduler config lives in the existing `settings` table (JSONB). A new `scheduler_runs` table records every execution outcome. The APScheduler cron binding is extended to INSERT a run record on job submission and UPDATE it on completion or failure. Six endpoints are served from a single new `schedulers.py` router.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy `text()`, Alembic, APScheduler (`EVENT_JOB_SUBMITTED`, `EVENT_JOB_EXECUTED`, `EVENT_JOB_ERROR`), pytest-asyncio + httpx ASGITransport.

**Spec:** `docs/superpowers/tasks/2026-09-09-epmcdme-10682-schedulers/spec.md`

## Global Constraints

- All new SQLModel tables use `__table_args__ = {"schema": "codemie"}`.
- Alembic migration `down_revision` must be `"fa14587c0de1"` (current HEAD).
- Auth via `user: User = Depends(authenticate)` on every endpoint.
- Error raising: `raise_not_found(id, resource_type)` from `codemie.rest_api.routers.utils`.
- `flag_modified(obj, "credential_values")` required before every JSONB update in `settings`.
- Response field for list payloads is `items` (not `data`) to match the FE contract.
- Pagination is zero-based: `page: int = 0`, `per_page: int = 10`.
- All new test files: pytest-asyncio + `httpx.AsyncClient(transport=ASGITransport(app=app))`. Auth mocked via `app.dependency_overrides[authenticate]`. Services mocked via `unittest.mock.patch` at the router import path.
- Commit prefix: `EPMCDME-10682:`.

---

## File Map

| Action | Path |
|---|---|
| Create | `src/codemie/rest_api/models/scheduler_run.py` |
| Create | `src/external/alembic/versions/<rev>_add_scheduler_runs_table.py` |
| Modify | `src/external/alembic/env.py` |
| Create | `src/codemie/repository/scheduler_run_repository.py` |
| Create | `src/codemie/service/scheduler_run_service.py` |
| Modify | `src/codemie/service/settings/scheduler_settings_service.py` |
| Create | `src/codemie/rest_api/routers/schedulers.py` |
| Modify | `src/codemie/rest_api/main.py` |
| Modify | `src/codemie/triggers/bindings/cron.py` |
| Modify | `src/codemie/triggers/actors/assistant.py` |
| Modify | `src/codemie/triggers/actors/workflow.py` |
| Modify | `src/codemie/rest_api/routers/utils.py` |
| Create | `tests/codemie/rest_api/routers/test_schedulers.py` |
| Modify | `tests/codemie/triggers/bindings/test_cron.py` |

---

## Task 1: SchedulerRun SQLModel, response models, and Alembic migration

**Files:**
- Create: `src/codemie/rest_api/models/scheduler_run.py`
- Create: `src/external/alembic/versions/<rev>_add_scheduler_runs_table.py`
- Modify: `src/external/alembic/env.py`

**Interfaces:**
- Produces: `SchedulerRun` (SQLModel table class), `SchedulerRunStatus` (StrEnum), `SchedulerListItem`, `SchedulerRunListItem`, `SchedulerRunDetail`, `SchedulerRunStats`, `SchedulersPaginatedResponse`, `SchedulerRunsPaginatedResponse`, `PatchSchedulerRequest`

- [ ] **Step 1: Write `src/codemie/rest_api/models/scheduler_run.py`**

```python
from __future__ import annotations
from datetime import datetime
from enum import StrEnum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel
from sqlmodel import Field, SQLModel
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB

from codemie.rest_api.models.base import PaginationData


class SchedulerRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SchedulerRun(SQLModel, table=True):
    __tablename__ = "scheduler_runs"
    __table_args__ = {"schema": "codemie"}

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    scheduler_id: Optional[str] = Field(default=None, index=True, foreign_key="codemie.settings.id")
    status: str = Field(index=True)
    trigger: str = Field(default="scheduled")
    started_at: datetime = Field(index=True)
    finished_at: Optional[datetime] = Field(default=None)
    duration_ms: Optional[int] = Field(default=None)
    execution_id: Optional[str] = Field(default=None)
    conversation_id: Optional[str] = Field(default=None)
    resource_execution_id: Optional[str] = Field(default=None)
    input_data: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSONB)
    )
    result_data: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSONB)
    )
    error_data: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSONB)
    )
    logs: Optional[list[dict[str, Any]]] = Field(
        default=None, sa_column=Column(JSONB)
    )
    metrics: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSONB)
    )


# ── Pydantic response models ──────────────────────────────────────────────────

class ResourceRef(BaseModel):
    id: str
    name: str
    type: str  # "Assistant" | "Workflow" | "Datasource"


class ProjectRef(BaseModel):
    id: str
    name: str


class ScheduleInfo(BaseModel):
    cron: str
    description: str
    timezone: str
    nextRunAt: Optional[str] = None


class LastRunRef(BaseModel):
    id: str
    status: str
    startedAt: str


class SchedulerListItem(BaseModel):
    id: str
    name: str
    resource: ResourceRef
    project: ProjectRef
    schedule: ScheduleInfo
    isEnabled: bool
    lastRun: Optional[LastRunRef] = None


class SchedulersPaginatedResponse(BaseModel):
    items: list[SchedulerListItem]
    pagination: PaginationData


class PatchSchedulerRequest(BaseModel):
    isEnabled: bool


class SchedulerRunListItem(BaseModel):
    id: str
    scheduler: dict[str, str]   # {id, name}
    resource: ResourceRef
    project: ProjectRef
    status: str
    trigger: str
    startedAt: str
    finishedAt: Optional[str] = None
    durationMs: Optional[int] = None
    executionId: Optional[str] = None
    error: Optional[dict[str, Any]] = None


class SchedulerRunsPaginatedResponse(BaseModel):
    items: list[SchedulerRunListItem]
    pagination: PaginationData


class SchedulerRunStats(BaseModel):
    total: int
    completed: int
    failed: int
    running: int
    cancelled: int
    successRate: float
    averageDurationMs: float


class SchedulerConfig(BaseModel):
    cron: str
    humanReadableSchedule: str
    timezone: str


class RunResult(BaseModel):
    available: bool
    content: Optional[Any] = None


class RunMetrics(BaseModel):
    inputTokens: Optional[int] = None
    outputTokens: Optional[int] = None
    cost: Optional[float] = None


class LogEntry(BaseModel):
    id: Optional[str] = None
    timestamp: str
    level: str
    message: str
    step: Optional[str] = None


class SchedulerRunDetail(BaseModel):
    id: str
    scheduler: dict[str, str]
    resource: ResourceRef
    project: ProjectRef
    status: str
    trigger: str
    startedAt: str
    finishedAt: Optional[str] = None
    durationMs: Optional[int] = None
    executionId: Optional[str] = None
    schedulerConfig: Optional[SchedulerConfig] = None
    input: Optional[dict[str, Any]] = None
    result: RunResult
    logs: list[LogEntry]
    metrics: Optional[RunMetrics] = None
    conversationId: Optional[str] = None
    resourceExecutionId: Optional[str] = None


class SchedulerRunLogsPaginatedResponse(BaseModel):
    items: list[LogEntry]
    pagination: PaginationData
```

- [ ] **Step 2: Generate the Alembic migration**

```bash
cd src && poetry run alembic revision --autogenerate -m "add_scheduler_runs_table"
```

Open the generated file under `src/external/alembic/versions/` and verify the `upgrade()` body. The autogenerate may miss the FK cascade — edit it to match:

```python
# Expected upgrade() body — adjust the generated file to match this exactly:
def upgrade() -> None:
    op.create_table(
        "scheduler_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scheduler_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("execution_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("resource_execution_id", sa.String(), nullable=True),
        sa.Column("input_data", postgresql.JSONB(), nullable=True),
        sa.Column("result_data", postgresql.JSONB(), nullable=True),
        sa.Column("error_data", postgresql.JSONB(), nullable=True),
        sa.Column("logs", postgresql.JSONB(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["scheduler_id"], ["codemie.settings.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="codemie",
    )
    op.create_index("ix_codemie_scheduler_runs_scheduler_id", "scheduler_runs", ["scheduler_id"], schema="codemie")
    op.create_index("ix_codemie_scheduler_runs_started_at", "scheduler_runs", ["started_at"], schema="codemie")
    op.create_index("ix_codemie_scheduler_runs_status", "scheduler_runs", ["status"], schema="codemie")


def downgrade() -> None:
    op.drop_index("ix_codemie_scheduler_runs_status", table_name="scheduler_runs", schema="codemie")
    op.drop_index("ix_codemie_scheduler_runs_started_at", table_name="scheduler_runs", schema="codemie")
    op.drop_index("ix_codemie_scheduler_runs_scheduler_id", table_name="scheduler_runs", schema="codemie")
    op.drop_table("scheduler_runs", schema="codemie")
```

Confirm `down_revision = "fa14587c0de1"`.

- [ ] **Step 3: Register `SchedulerRun` in `src/external/alembic/env.py`**

Find the block of model imports before `target_metadata = SQLModel.metadata` and add:

```python
from codemie.rest_api.models.scheduler_run import SchedulerRun  # noqa: F401
```

- [ ] **Step 4: Verify autogenerate detects no further changes**

```bash
cd src && poetry run alembic check
```

Expected: `No new upgrade operations detected.`

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/scheduler_run.py \
        src/external/alembic/versions/*add_scheduler_runs_table.py \
        src/external/alembic/env.py
git commit -m "EPMCDME-10682: Add SchedulerRun SQLModel, response models, and migration"
```

---

## Task 2: SchedulerRunRepository

**Files:**
- Create: `src/codemie/repository/scheduler_run_repository.py`

**Interfaces:**
- Consumes: `SchedulerRun` from `codemie.rest_api.models.scheduler_run`
- Produces:
  - `SchedulerRunRepository.list_runs(filters: dict) -> tuple[list[SchedulerRun], int]`
  - `SchedulerRunRepository.get_stats(filters: dict) -> dict`
  - `SchedulerRunRepository.get_by_id(run_id: str) -> SchedulerRun | None`
  - `SchedulerRunRepository.get_logs_page(run_id: str, page: int, per_page: int) -> tuple[list[dict], int]`
  - `SchedulerRunRepository.create(run: SchedulerRun) -> SchedulerRun`
  - `SchedulerRunRepository.update(run_id: str, fields: dict) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/rest_api/routers/test_schedulers.py` with the repository unit test:

```python
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from codemie.repository.scheduler_run_repository import SchedulerRunRepository
from codemie.rest_api.models.scheduler_run import SchedulerRun, SchedulerRunStatus


def _make_run(scheduler_id="sched-1", status="completed") -> SchedulerRun:
    return SchedulerRun(
        id="run-1",
        scheduler_id=scheduler_id,
        status=status,
        trigger="scheduled",
        started_at=datetime(2026, 9, 9, 9, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 9, 9, 0, 18, tzinfo=timezone.utc),
        duration_ms=18000,
    )


def test_repository_get_by_id_returns_run():
    repo = SchedulerRunRepository()
    run = _make_run()
    with patch.object(SchedulerRun, "get_by_id", return_value=run):
        result = repo.get_by_id("run-1")
    assert result.id == "run-1"
    assert result.status == "completed"


def test_repository_get_by_id_returns_none_for_missing():
    repo = SchedulerRunRepository()
    with patch.object(SchedulerRun, "get_by_id", return_value=None):
        result = repo.get_by_id("nonexistent")
    assert result is None
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_repository_get_by_id_returns_run -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `scheduler_run_repository` does not exist yet.

- [ ] **Step 3: Write `src/codemie/repository/scheduler_run_repository.py`**

```python
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import text

from codemie.rest_api.models.scheduler_run import SchedulerRun


class SchedulerRunRepository:
    """Data access for scheduler run records."""

    def create(self, run: SchedulerRun) -> SchedulerRun:
        return run.save()

    def update(self, run_id: str, fields: dict[str, Any]) -> None:
        run = SchedulerRun.get_by_id(run_id)
        if run is None:
            return
        for key, value in fields.items():
            setattr(run, key, value)
        run.update()

    def get_by_id(self, run_id: str) -> Optional[SchedulerRun]:
        return SchedulerRun.get_by_id(run_id)

    def list_runs(
        self,
        scheduler_id: Optional[str] = None,
        status: Optional[str] = None,
        project: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        sort_direction: str = "desc",
        page: int = 0,
        per_page: int = 10,
    ) -> tuple[list[SchedulerRun], int]:
        conditions = ["sr.scheduler_id IS NOT NULL"]
        params: dict[str, Any] = {}

        if scheduler_id:
            conditions.append("sr.scheduler_id = :scheduler_id")
            params["scheduler_id"] = scheduler_id
        if status:
            conditions.append("sr.status = :status")
            params["status"] = status
        if date_from:
            conditions.append("sr.started_at >= :date_from")
            params["date_from"] = date_from
        if date_to:
            conditions.append("sr.started_at <= :date_to")
            params["date_to"] = date_to

        where = "WHERE " + " AND ".join(conditions)
        order = "ASC" if sort_direction.lower() == "asc" else "DESC"
        params.update({"limit": per_page, "offset": page * per_page})

        count_sql = text(f"SELECT COUNT(*) FROM codemie.scheduler_runs sr {where}")
        rows_sql = text(
            f"SELECT * FROM codemie.scheduler_runs sr {where} "
            f"ORDER BY sr.started_at {order} "
            f"LIMIT :limit OFFSET :offset"
        )

        with SchedulerRun.get_engine().connect() as conn:
            total = conn.execute(count_sql.bindparams(**params)).scalar_one()
            rows = conn.execute(rows_sql.bindparams(**params)).mappings().all()

        runs = [SchedulerRun(**dict(row)) for row in rows]
        return runs, total

    def get_stats(
        self,
        scheduler_id: Optional[str] = None,
        status: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> dict[str, Any]:
        conditions = ["scheduler_id IS NOT NULL"]
        params: dict[str, Any] = {}

        if scheduler_id:
            conditions.append("scheduler_id = :scheduler_id")
            params["scheduler_id"] = scheduler_id
        if date_from:
            conditions.append("started_at >= :date_from")
            params["date_from"] = date_from
        if date_to:
            conditions.append("started_at <= :date_to")
            params["date_to"] = date_to

        where = "WHERE " + " AND ".join(conditions)

        sql = text(
            f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                COUNT(*) FILTER (WHERE status = 'running') AS running,
                COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL
                    AND status IN ('completed', 'failed')) AS avg_duration_ms
            FROM codemie.scheduler_runs
            {where}
            """
        )

        with SchedulerRun.get_engine().connect() as conn:
            row = conn.execute(sql.bindparams(**params)).mappings().one()

        completed = row["completed"] or 0
        failed = row["failed"] or 0
        denominator = completed + failed
        success_rate = round(completed / denominator * 100, 2) if denominator > 0 else 0.0

        return {
            "total": row["total"] or 0,
            "completed": completed,
            "failed": failed,
            "running": row["running"] or 0,
            "cancelled": row["cancelled"] or 0,
            "successRate": success_rate,
            "averageDurationMs": float(row["avg_duration_ms"] or 0),
        }

    def get_logs_page(
        self, run_id: str, page: int = 0, per_page: int = 50
    ) -> tuple[list[dict[str, Any]], int]:
        run = SchedulerRun.get_by_id(run_id)
        if run is None or not run.logs:
            return [], 0
        total = len(run.logs)
        start = page * per_page
        return run.logs[start : start + per_page], total
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_repository_get_by_id_returns_run ../tests/codemie/rest_api/routers/test_schedulers.py::test_repository_get_by_id_returns_none_for_missing -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/scheduler_run_repository.py \
        tests/codemie/rest_api/routers/test_schedulers.py
git commit -m "EPMCDME-10682: Add SchedulerRunRepository with list, stats, get, and logs"
```

---

## Task 3: SchedulerRunService

**Files:**
- Create: `src/codemie/service/scheduler_run_service.py`

**Interfaces:**
- Consumes: `SchedulerRunRepository` from Task 2
- Produces:
  - `SchedulerRunService.list_runs(filters: dict) -> tuple[list[SchedulerRun], int]`
  - `SchedulerRunService.get_stats(filters: dict) -> SchedulerRunStats`
  - `SchedulerRunService.get_run(run_id: str) -> SchedulerRun`  (raises `raise_not_found` if absent)
  - `SchedulerRunService.get_logs(run_id: str, page: int, per_page: int) -> tuple[list[dict], int]`

- [ ] **Step 1: Write the failing test (append to `test_schedulers.py`)**

```python
from unittest.mock import patch, MagicMock
from codemie.service.scheduler_run_service import SchedulerRunService


def test_service_get_run_raises_not_found_for_missing():
    with patch(
        "codemie.service.scheduler_run_service.SchedulerRunRepository"
    ) as MockRepo:
        MockRepo.return_value.get_by_id.return_value = None
        import pytest
        from codemie.core.exceptions import NotFoundException
        with pytest.raises(NotFoundException):
            SchedulerRunService().get_run("nonexistent-id")


def test_service_get_stats_returns_stats_model():
    with patch(
        "codemie.service.scheduler_run_service.SchedulerRunRepository"
    ) as MockRepo:
        MockRepo.return_value.get_stats.return_value = {
            "total": 10, "completed": 8, "failed": 2,
            "running": 0, "cancelled": 0,
            "successRate": 80.0, "averageDurationMs": 5000.0,
        }
        stats = SchedulerRunService().get_stats({})
    assert stats.total == 10
    assert stats.successRate == 80.0
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_service_get_run_raises_not_found_for_missing -v
```

Expected: `ImportError` — `scheduler_run_service` does not exist.

- [ ] **Step 3: Write `src/codemie/service/scheduler_run_service.py`**

```python
from __future__ import annotations

from codemie.repository.scheduler_run_repository import SchedulerRunRepository
from codemie.rest_api.models.scheduler_run import SchedulerRun, SchedulerRunStats
from codemie.rest_api.routers.utils import raise_not_found


class SchedulerRunService:
    def __init__(self) -> None:
        self._repo = SchedulerRunRepository()

    def list_runs(self, **kwargs) -> tuple[list[SchedulerRun], int]:
        return self._repo.list_runs(**kwargs)

    def get_stats(self, filters: dict) -> SchedulerRunStats:
        raw = self._repo.get_stats(**filters)
        return SchedulerRunStats(**raw)

    def get_run(self, run_id: str) -> SchedulerRun:
        run = self._repo.get_by_id(run_id)
        if run is None:
            raise_not_found(run_id, "SchedulerRun")
        return run

    def get_logs(self, run_id: str, page: int = 0, per_page: int = 50) -> tuple[list[dict], int]:
        self.get_run(run_id)  # raises 404 if missing
        return self._repo.get_logs_page(run_id, page, per_page)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_service_get_run_raises_not_found_for_missing ../tests/codemie/rest_api/routers/test_schedulers.py::test_service_get_stats_returns_stats_model -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/scheduler_run_service.py \
        tests/codemie/rest_api/routers/test_schedulers.py
git commit -m "EPMCDME-10682: Add SchedulerRunService"
```

---

## Task 4: SchedulerSettingsService — list_schedulers and patch_is_enabled

**Files:**
- Modify: `src/codemie/service/settings/scheduler_settings_service.py`

**Interfaces:**
- Consumes: existing `Settings`, `SchedulerRun` (for lastRun subquery)
- Produces:
  - `SchedulerSettingsService.list_schedulers(page, per_page, search, resource_type, project_id, resource_id, status, last_run_status) -> tuple[list[SchedulerListItem], int]`
  - `SchedulerSettingsService.patch_is_enabled(scheduler_id: str, is_enabled: bool) -> SchedulerListItem`

- [ ] **Step 1: Write the failing test (append to `test_schedulers.py`)**

```python
from unittest.mock import patch, MagicMock
from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService


def test_patch_is_enabled_calls_flag_modified():
    mock_setting = MagicMock()
    mock_setting.credential_values = [MagicMock(value="is_enabled")]

    with (
        patch("codemie.service.settings.scheduler_settings_service.Settings.get_by_id",
              return_value=mock_setting),
        patch("codemie.service.settings.scheduler_settings_service.flag_modified") as mock_flag,
        patch.object(mock_setting, "update"),
        patch(
            "codemie.service.settings.scheduler_settings_service.SchedulerSettingsService._build_scheduler_item",
            return_value=MagicMock(),
        ),
    ):
        SchedulerSettingsService.patch_is_enabled("sched-1", True)
        mock_flag.assert_called_once_with(mock_setting, "credential_values")


def test_patch_is_enabled_raises_not_found():
    with patch("codemie.service.settings.scheduler_settings_service.Settings.get_by_id",
               return_value=None):
        from codemie.core.exceptions import NotFoundException
        import pytest
        with pytest.raises(NotFoundException):
            SchedulerSettingsService.patch_is_enabled("missing", True)
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_patch_is_enabled_calls_flag_modified -v
```

Expected: `AttributeError` — `patch_is_enabled` not defined.

- [ ] **Step 3: Add `list_schedulers`, `patch_is_enabled`, and `_build_scheduler_item` to `SchedulerSettingsService`**

Append the following static/class methods to the `SchedulerSettingsService` class in `src/codemie/service/settings/scheduler_settings_service.py`:

```python
    # ── New methods for EPMCDME-10682 ────────────────────────────────────────

    @staticmethod
    def _get_cred_value(setting: Settings, key: str):
        """Extract a single key from the first credential_values entry."""
        if not setting.credential_values:
            return None
        cv = setting.credential_values[0]
        return getattr(cv, key, None) or (
            cv.value if hasattr(cv, "value") else
            (cv.get(key) if isinstance(cv, dict) else None)
        )

    @staticmethod
    def _build_scheduler_item(setting: Settings, last_run=None) -> "SchedulerListItem":
        from codemie.rest_api.models.scheduler_run import (
            SchedulerListItem, ResourceRef, ProjectRef, ScheduleInfo, LastRunRef
        )
        from croniter import croniter
        import cron_descriptor  # optional; fall back to raw cron if unavailable

        cron_expr = SchedulerSettingsService._get_cred_value(setting, "schedule") or ""
        timezone = SchedulerSettingsService._get_cred_value(setting, "timezone") or "UTC"
        resource_type = SchedulerSettingsService._get_cred_value(setting, "resource_type") or ""
        resource_id = SchedulerSettingsService._get_cred_value(setting, "resource_id") or ""
        resource_name = SchedulerSettingsService._get_cred_value(setting, "resource_name") or ""
        is_enabled = SchedulerSettingsService._get_cred_value(setting, "is_enabled") or False

        try:
            description = cron_descriptor.get_description(cron_expr)
        except Exception:
            description = cron_expr

        next_run_at = None
        if cron_expr:
            try:
                import pytz
                from datetime import datetime
                tz = pytz.timezone(timezone)
                itr = croniter(cron_expr, datetime.now(tz))
                next_run_at = itr.get_next(datetime).isoformat()
            except Exception:
                pass

        last_run_item = None
        if last_run:
            last_run_item = LastRunRef(
                id=last_run["id"],
                status=last_run["status"],
                startedAt=last_run["started_at"].isoformat() if hasattr(last_run["started_at"], "isoformat") else str(last_run["started_at"]),
            )

        return SchedulerListItem(
            id=setting.id,
            name=setting.alias or resource_name or setting.id,
            resource=ResourceRef(
                id=resource_id,
                name=resource_name,
                type=resource_type.capitalize(),
            ),
            project=ProjectRef(id=setting.project_name, name=setting.project_name),
            schedule=ScheduleInfo(
                cron=cron_expr,
                description=description,
                timezone=timezone,
                nextRunAt=next_run_at,
            ),
            isEnabled=bool(is_enabled),
            lastRun=last_run_item,
        )

    @staticmethod
    def list_schedulers(
        page: int = 0,
        per_page: int = 10,
        search: Optional[str] = None,
        resource_type: Optional[str] = None,
        project_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        status: Optional[str] = None,
        last_run_status: Optional[str] = None,
    ):
        from sqlalchemy import text
        from codemie.rest_api.models.scheduler_run import SchedulersPaginatedResponse
        from codemie_tools.base.models import CredentialTypes

        conditions = ["s.credential_type = 'Scheduler'"]
        params: dict = {}

        if project_id:
            conditions.append("s.project_name = :project_id")
            params["project_id"] = project_id
        if status == "enabled":
            conditions.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(s.credential_values::jsonb) cv "
                "WHERE (cv->>'is_enabled')::boolean = true)"
            )
        elif status == "disabled":
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM jsonb_array_elements(s.credential_values::jsonb) cv "
                "WHERE (cv->>'is_enabled')::boolean = true)"
            )

        last_run_join = (
            "LEFT JOIN LATERAL ("
            "  SELECT id, status, started_at FROM codemie.scheduler_runs"
            "  WHERE scheduler_id = s.id"
            "  ORDER BY started_at DESC LIMIT 1"
            ") lr ON true"
        )

        if last_run_status == "never":
            conditions.append("lr.id IS NULL")
        elif last_run_status:
            conditions.append("lr.status = :last_run_status")
            params["last_run_status"] = last_run_status

        where = "WHERE " + " AND ".join(conditions)

        count_sql = text(
            f"SELECT COUNT(*) FROM codemie.settings s {last_run_join} {where}"
        )
        rows_sql = text(
            f"SELECT s.*, lr.id AS lr_id, lr.status AS lr_status, lr.started_at AS lr_started_at "
            f"FROM codemie.settings s {last_run_join} {where} "
            f"ORDER BY s.id LIMIT :limit OFFSET :offset"
        )
        params.update({"limit": per_page, "offset": page * per_page})

        with Settings.get_engine().connect() as conn:
            total = conn.execute(count_sql.bindparams(**params)).scalar_one()
            rows = conn.execute(rows_sql.bindparams(**params)).mappings().all()

        items = []
        for row in rows:
            setting = Settings.get_by_id(row["id"])
            last_run = None
            if row.get("lr_id"):
                last_run = {
                    "id": row["lr_id"],
                    "status": row["lr_status"],
                    "started_at": row["lr_started_at"],
                }
            items.append(SchedulerSettingsService._build_scheduler_item(setting, last_run))

        from codemie.rest_api.models.base import PaginationData
        import math
        pagination = PaginationData(
            page=page,
            per_page=per_page,
            total=total,
            pages=math.ceil(total / per_page) if per_page else 0,
        )
        return SchedulersPaginatedResponse(items=items, pagination=pagination)

    @staticmethod
    def patch_is_enabled(scheduler_id: str, is_enabled: bool):
        from codemie.rest_api.routers.utils import raise_not_found

        setting = Settings.get_by_id(scheduler_id)
        if setting is None:
            raise_not_found(scheduler_id, "Scheduler")

        if setting.credential_values:
            cv = setting.credential_values[0]
            if isinstance(cv, dict):
                cv["is_enabled"] = is_enabled
            else:
                cv.is_enabled = is_enabled

        flag_modified(setting, "credential_values")
        setting.update()
        return SchedulerSettingsService._build_scheduler_item(setting)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_patch_is_enabled_calls_flag_modified ../tests/codemie/rest_api/routers/test_schedulers.py::test_patch_is_enabled_raises_not_found -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/settings/scheduler_settings_service.py \
        tests/codemie/rest_api/routers/test_schedulers.py
git commit -m "EPMCDME-10682: Add list_schedulers and patch_is_enabled to SchedulerSettingsService"
```

---

## Task 5: Phase 1 router — GET /v1/schedulers and PATCH /v1/schedulers/{id}

**Files:**
- Create: `src/codemie/rest_api/routers/schedulers.py`
- Modify: `src/codemie/rest_api/main.py`

**Interfaces:**
- Consumes: `SchedulerSettingsService.list_schedulers`, `SchedulerSettingsService.patch_is_enabled`, `SchedulerRunService` from Task 3
- Produces: mounted router at `/v1/schedulers`

- [ ] **Step 1: Write the failing router tests (append to `test_schedulers.py`)**

```python
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock

from codemie.rest_api.main import app
from codemie.rest_api.security.authentication import authenticate, User
from codemie.rest_api.models.scheduler_run import (
    SchedulerListItem, ResourceRef, ProjectRef, ScheduleInfo, SchedulersPaginatedResponse
)
from codemie.rest_api.models.base import PaginationData


def _mock_user():
    return User(id="user-1", email="test@test.com", project_name="epm-cdme")


def _mock_scheduler_item():
    return SchedulerListItem(
        id="sched-1",
        name="Daily Jira Report",
        resource=ResourceRef(id="asst-1", name="Jira Reporter", type="Assistant"),
        project=ProjectRef(id="epm-cdme", name="epm-cdme"),
        schedule=ScheduleInfo(
            cron="0 9 * * 1-5",
            description="Weekdays at 09:00",
            timezone="Europe/Kiev",
        ),
        isEnabled=True,
    )


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[authenticate] = _mock_user
    yield
    app.dependency_overrides.pop(authenticate, None)


@pytest.mark.anyio
async def test_list_schedulers_returns_200():
    paginated = SchedulersPaginatedResponse(
        items=[_mock_scheduler_item()],
        pagination=PaginationData(page=0, per_page=10, total=1, pages=1),
    )
    with patch(
        "codemie.rest_api.routers.schedulers.SchedulerSettingsService.list_schedulers",
        return_value=paginated,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/v1/schedulers")
    assert response.status_code == 200
    data = response.json()
    assert data["items"][0]["id"] == "sched-1"
    assert data["pagination"]["total"] == 1


@pytest.mark.anyio
async def test_patch_scheduler_returns_200():
    with patch(
        "codemie.rest_api.routers.schedulers.SchedulerSettingsService.patch_is_enabled",
        return_value=_mock_scheduler_item(),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.patch("/v1/schedulers/sched-1", json={"isEnabled": False})
    assert response.status_code == 200
    assert response.json()["id"] == "sched-1"
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_list_schedulers_returns_200 -v
```

Expected: `404` or route not found — router not mounted yet.

- [ ] **Step 3: Create `src/codemie/rest_api/routers/schedulers.py`**

```python
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, status

from codemie.rest_api.models.scheduler_run import (
    PatchSchedulerRequest,
    SchedulerListItem,
    SchedulersPaginatedResponse,
)
from codemie.rest_api.security.authentication import User, authenticate
from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

router = APIRouter(tags=["Schedulers"], prefix="/v1")


@router.get(
    "/schedulers",
    status_code=status.HTTP_200_OK,
    response_model=SchedulersPaginatedResponse,
)
def list_schedulers(
    page: int = 0,
    pageSize: int = 10,
    search: Optional[str] = None,
    resourceType: Optional[str] = None,
    projectId: Optional[str] = None,
    resourceId: Optional[str] = None,
    status_filter: Optional[str] = None,
    lastRunStatus: Optional[str] = None,
    user: User = Depends(authenticate),
):
    return SchedulerSettingsService.list_schedulers(
        page=page,
        per_page=pageSize,
        search=search,
        resource_type=resourceType,
        project_id=projectId,
        resource_id=resourceId,
        status=status_filter,
        last_run_status=lastRunStatus,
    )


@router.patch(
    "/schedulers/{scheduler_id}",
    status_code=status.HTTP_200_OK,
    response_model=SchedulerListItem,
)
def patch_scheduler(
    scheduler_id: str,
    body: PatchSchedulerRequest,
    user: User = Depends(authenticate),
):
    return SchedulerSettingsService.patch_is_enabled(scheduler_id, body.isEnabled)
```

- [ ] **Step 4: Register the router in `src/codemie/rest_api/main.py`**

Find the block of `app.include_router(...)` calls (around line 658) and add:

```python
from codemie.rest_api.routers import schedulers as schedulers_router
# ...existing include_router calls...
app.include_router(schedulers_router.router)
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_list_schedulers_returns_200 ../tests/codemie/rest_api/routers/test_schedulers.py::test_patch_scheduler_returns_200 -v
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/schedulers.py \
        src/codemie/rest_api/main.py \
        tests/codemie/rest_api/routers/test_schedulers.py
git commit -m "EPMCDME-10682: Phase 1 — GET /v1/schedulers and PATCH /v1/schedulers/{id}"
```

---

## Task 6: Phase 2 router — GET /v1/scheduler-runs and GET /v1/scheduler-runs/stats

**Files:**
- Modify: `src/codemie/rest_api/routers/schedulers.py`

**Interfaces:**
- Consumes: `SchedulerRunService.list_runs`, `SchedulerRunService.get_stats`
- Produces: endpoints at `/v1/scheduler-runs` and `/v1/scheduler-runs/stats`

- [ ] **Step 1: Write the failing tests (append to `test_schedulers.py`)**

```python
from codemie.rest_api.models.scheduler_run import (
    SchedulerRunListItem, SchedulerRunsPaginatedResponse, SchedulerRunStats, SchedulerRun
)
from datetime import datetime, timezone


def _mock_run_list_item():
    return SchedulerRunListItem(
        id="run-1",
        scheduler={"id": "sched-1", "name": "Daily Jira Report"},
        resource=ResourceRef(id="asst-1", name="Jira Reporter", type="Assistant"),
        project=ProjectRef(id="epm-cdme", name="epm-cdme"),
        status="completed",
        trigger="scheduled",
        startedAt="2026-09-09T09:00:00+00:00",
        durationMs=18000,
    )


@pytest.mark.anyio
async def test_list_scheduler_runs_returns_200():
    paginated = SchedulerRunsPaginatedResponse(
        items=[_mock_run_list_item()],
        pagination=PaginationData(page=0, per_page=10, total=1, pages=1),
    )
    with patch(
        "codemie.rest_api.routers.schedulers.SchedulerRunService.list_runs",
        return_value=paginated,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/v1/scheduler-runs?schedulerId=sched-1")
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "run-1"


@pytest.mark.anyio
async def test_scheduler_runs_stats_returns_200():
    stats = SchedulerRunStats(
        total=10, completed=8, failed=2, running=0, cancelled=0,
        successRate=80.0, averageDurationMs=5000.0,
    )
    with patch(
        "codemie.rest_api.routers.schedulers.SchedulerRunService.get_stats",
        return_value=stats,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/v1/scheduler-runs/stats")
    assert response.status_code == 200
    assert response.json()["successRate"] == 80.0
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_list_scheduler_runs_returns_200 -v
```

Expected: `404` — endpoints not mounted.

- [ ] **Step 3: Add Phase 2 endpoints to `src/codemie/rest_api/routers/schedulers.py`**

Add these imports at the top:

```python
from codemie.rest_api.models.scheduler_run import (
    PatchSchedulerRequest,
    SchedulerListItem,
    SchedulersPaginatedResponse,
    SchedulerRunsPaginatedResponse,
    SchedulerRunStats,
)
from codemie.service.scheduler_run_service import SchedulerRunService
```

Add these endpoints after the existing Phase 1 routes:

```python
@router.get(
    "/scheduler-runs/stats",
    status_code=status.HTTP_200_OK,
    response_model=SchedulerRunStats,
)
def get_scheduler_run_stats(
    schedulerId: Optional[str] = None,
    status_filter: Optional[str] = None,
    resourceType: Optional[str] = None,
    resourceId: Optional[str] = None,
    dateFrom: Optional[str] = None,
    dateTo: Optional[str] = None,
    user: User = Depends(authenticate),
):
    from datetime import datetime
    filters = dict(
        scheduler_id=schedulerId,
        status=status_filter,
        resource_type=resourceType,
        resource_id=resourceId,
        date_from=datetime.fromisoformat(dateFrom) if dateFrom else None,
        date_to=datetime.fromisoformat(dateTo) if dateTo else None,
    )
    return SchedulerRunService().get_stats({k: v for k, v in filters.items() if v is not None})


@router.get(
    "/scheduler-runs",
    status_code=status.HTTP_200_OK,
    response_model=SchedulerRunsPaginatedResponse,
)
def list_scheduler_runs(
    page: int = 0,
    pageSize: int = 10,
    schedulerId: Optional[str] = None,
    status_filter: Optional[str] = None,
    project: Optional[str] = None,
    resourceType: Optional[str] = None,
    resourceId: Optional[str] = None,
    search: Optional[str] = None,
    dateFrom: Optional[str] = None,
    dateTo: Optional[str] = None,
    sortDirection: str = "desc",
    user: User = Depends(authenticate),
):
    from datetime import datetime
    from codemie.rest_api.models.base import PaginationData
    import math

    runs, total = SchedulerRunService().list_runs(
        scheduler_id=schedulerId,
        status=status_filter,
        project=project,
        resource_type=resourceType,
        resource_id=resourceId,
        search=search,
        date_from=datetime.fromisoformat(dateFrom) if dateFrom else None,
        date_to=datetime.fromisoformat(dateTo) if dateTo else None,
        sort_direction=sortDirection,
        page=page,
        per_page=pageSize,
    )

    from codemie.rest_api.models.scheduler_run import SchedulerRunListItem, ResourceRef, ProjectRef
    from codemie.rest_api.models.settings import Settings

    # Enrich runs with scheduler and resource names
    scheduler_cache: dict = {}
    items = []
    for run in runs:
        if run.scheduler_id and run.scheduler_id not in scheduler_cache:
            s = Settings.get_by_id(run.scheduler_id)
            scheduler_cache[run.scheduler_id] = s
        setting = scheduler_cache.get(run.scheduler_id)
        scheduler_name = setting.alias if setting else ""
        resource_type_val = (
            SchedulerSettingsService._get_cred_value(setting, "resource_type") or ""
            if setting else ""
        )
        resource_id_val = (
            SchedulerSettingsService._get_cred_value(setting, "resource_id") or ""
            if setting else ""
        )
        resource_name_val = (
            SchedulerSettingsService._get_cred_value(setting, "resource_name") or ""
            if setting else ""
        )
        project_name = setting.project_name if setting else ""

        items.append(
            SchedulerRunListItem(
                id=run.id,
                scheduler={"id": run.scheduler_id or "", "name": scheduler_name},
                resource=ResourceRef(
                    id=resource_id_val,
                    name=resource_name_val,
                    type=resource_type_val.capitalize(),
                ),
                project=ProjectRef(id=project_name, name=project_name),
                status=run.status,
                trigger=run.trigger,
                startedAt=run.started_at.isoformat(),
                finishedAt=run.finished_at.isoformat() if run.finished_at else None,
                durationMs=run.duration_ms,
                executionId=run.execution_id,
                error=run.error_data,
            )
        )

    return SchedulerRunsPaginatedResponse(
        items=items,
        pagination=PaginationData(
            page=page,
            per_page=pageSize,
            total=total,
            pages=math.ceil(total / pageSize) if pageSize else 0,
        ),
    )
```

**Important:** The `/scheduler-runs/stats` route MUST be declared before `/scheduler-runs` in the file so FastAPI resolves the static path `stats` before treating it as a `{runId}` parameter. Already the case in this plan.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_list_scheduler_runs_returns_200 ../tests/codemie/rest_api/routers/test_schedulers.py::test_scheduler_runs_stats_returns_200 -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/routers/schedulers.py \
        tests/codemie/rest_api/routers/test_schedulers.py
git commit -m "EPMCDME-10682: Phase 2 — GET /v1/scheduler-runs and GET /v1/scheduler-runs/stats"
```

---

## Task 7: Phase 3 router — GET /v1/scheduler-runs/{runId} and GET /v1/scheduler-runs/{runId}/logs

**Files:**
- Modify: `src/codemie/rest_api/routers/schedulers.py`

**Interfaces:**
- Consumes: `SchedulerRunService.get_run`, `SchedulerRunService.get_logs`
- Produces: endpoints at `/v1/scheduler-runs/{runId}` and `/v1/scheduler-runs/{runId}/logs`

- [ ] **Step 1: Write the failing tests (append to `test_schedulers.py`)**

```python
from codemie.rest_api.models.scheduler_run import SchedulerRunDetail, RunResult


def _mock_run_detail():
    return SchedulerRunDetail(
        id="run-1",
        scheduler={"id": "sched-1", "name": "Daily Jira Report"},
        resource=ResourceRef(id="asst-1", name="Jira Reporter", type="Assistant"),
        project=ProjectRef(id="epm-cdme", name="epm-cdme"),
        status="completed",
        trigger="scheduled",
        startedAt="2026-09-09T09:00:00+00:00",
        result=RunResult(available=True, content="Report ready."),
        logs=[],
    )


@pytest.mark.anyio
async def test_get_run_detail_returns_200():
    with patch(
        "codemie.rest_api.routers.schedulers.SchedulerRunService.get_run_detail",
        return_value=_mock_run_detail(),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/v1/scheduler-runs/run-1")
    assert response.status_code == 200
    assert response.json()["id"] == "run-1"


@pytest.mark.anyio
async def test_get_run_detail_returns_404_for_missing():
    from codemie.core.exceptions import NotFoundException
    with patch(
        "codemie.rest_api.routers.schedulers.SchedulerRunService.get_run_detail",
        side_effect=NotFoundException("SchedulerRun not found"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/v1/scheduler-runs/nonexistent")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_run_logs_returns_200():
    with patch(
        "codemie.rest_api.routers.schedulers.SchedulerRunService.get_logs",
        return_value=([], 0),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/v1/scheduler-runs/run-1/logs")
    assert response.status_code == 200
    assert "items" in response.json()
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_get_run_detail_returns_200 -v
```

Expected: `404` — endpoint not defined.

- [ ] **Step 3: Add `get_run_detail` to `SchedulerRunService`**

Append to `src/codemie/service/scheduler_run_service.py`:

```python
    def get_run_detail(self, run_id: str) -> "SchedulerRunDetail":
        from codemie.rest_api.models.scheduler_run import (
            SchedulerRunDetail, ResourceRef, ProjectRef,
            SchedulerConfig, RunResult, RunMetrics, LogEntry
        )
        from codemie.rest_api.models.settings import Settings
        from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService
        import cron_descriptor

        run = self.get_run(run_id)
        setting = Settings.get_by_id(run.scheduler_id) if run.scheduler_id else None

        cron_expr = (
            SchedulerSettingsService._get_cred_value(setting, "schedule") or ""
            if setting else ""
        )
        timezone = (
            SchedulerSettingsService._get_cred_value(setting, "timezone") or "UTC"
            if setting else "UTC"
        )
        resource_type = (
            SchedulerSettingsService._get_cred_value(setting, "resource_type") or ""
            if setting else ""
        )
        resource_id = (
            SchedulerSettingsService._get_cred_value(setting, "resource_id") or ""
            if setting else ""
        )
        resource_name = (
            SchedulerSettingsService._get_cred_value(setting, "resource_name") or ""
            if setting else ""
        )
        project_name = setting.project_name if setting else ""
        scheduler_name = setting.alias if setting else ""

        try:
            human_readable = cron_descriptor.get_description(cron_expr)
        except Exception:
            human_readable = cron_expr

        sched_config = SchedulerConfig(
            cron=cron_expr,
            humanReadableSchedule=human_readable,
            timezone=f"{timezone} (UTC)",
        ) if cron_expr else None

        result_available = run.result_data is not None
        result_content = run.result_data.get("content") if run.result_data else None

        metrics = None
        if run.metrics:
            metrics = RunMetrics(
                inputTokens=run.metrics.get("inputTokens"),
                outputTokens=run.metrics.get("outputTokens"),
                cost=run.metrics.get("cost"),
            )

        logs = [
            LogEntry(**entry) for entry in (run.logs or [])
        ]

        return SchedulerRunDetail(
            id=run.id,
            scheduler={"id": run.scheduler_id or "", "name": scheduler_name},
            resource=ResourceRef(
                id=resource_id,
                name=resource_name,
                type=resource_type.capitalize(),
            ),
            project=ProjectRef(id=project_name, name=project_name),
            status=run.status,
            trigger=run.trigger,
            startedAt=run.started_at.isoformat(),
            finishedAt=run.finished_at.isoformat() if run.finished_at else None,
            durationMs=run.duration_ms,
            executionId=run.execution_id,
            schedulerConfig=sched_config,
            input=run.input_data,
            result=RunResult(available=result_available, content=result_content),
            logs=logs,
            metrics=metrics,
            conversationId=run.conversation_id,
            resourceExecutionId=run.resource_execution_id,
        )
```

- [ ] **Step 4: Add Phase 3 endpoints to `src/codemie/rest_api/routers/schedulers.py`**

Add these imports (extend existing import block):
```python
from codemie.rest_api.models.scheduler_run import (
    ...,
    SchedulerRunDetail,
    SchedulerRunLogsPaginatedResponse,
)
```

Add endpoints:

```python
@router.get(
    "/scheduler-runs/{run_id}/logs",
    status_code=status.HTTP_200_OK,
    response_model=SchedulerRunLogsPaginatedResponse,
)
def get_scheduler_run_logs(
    run_id: str,
    page: int = 0,
    pageSize: int = 50,
    user: User = Depends(authenticate),
):
    from codemie.rest_api.models.base import PaginationData
    from codemie.rest_api.models.scheduler_run import LogEntry
    import math

    logs_raw, total = SchedulerRunService().get_logs(run_id, page=page, per_page=pageSize)
    items = [LogEntry(**entry) for entry in logs_raw]
    return SchedulerRunLogsPaginatedResponse(
        items=items,
        pagination=PaginationData(
            page=page,
            per_page=pageSize,
            total=total,
            pages=math.ceil(total / pageSize) if pageSize else 0,
        ),
    )


@router.get(
    "/scheduler-runs/{run_id}",
    status_code=status.HTTP_200_OK,
    response_model=SchedulerRunDetail,
)
def get_scheduler_run(
    run_id: str,
    user: User = Depends(authenticate),
):
    return SchedulerRunService().get_run_detail(run_id)
```

**Important:** `/scheduler-runs/{run_id}/logs` MUST appear before `/scheduler-runs/{run_id}` in the file — FastAPI matches routes in declaration order.

- [ ] **Step 5: Run all Phase 3 tests**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_get_run_detail_returns_200 ../tests/codemie/rest_api/routers/test_schedulers.py::test_get_run_detail_returns_404_for_missing ../tests/codemie/rest_api/routers/test_schedulers.py::test_get_run_logs_returns_200 -v
```

Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/schedulers.py \
        src/codemie/service/scheduler_run_service.py \
        tests/codemie/rest_api/routers/test_schedulers.py
git commit -m "EPMCDME-10682: Phase 3 — GET /v1/scheduler-runs/{runId} and /logs"
```

---

## Task 8: cron.py — APScheduler run persistence

**Files:**
- Modify: `src/codemie/triggers/bindings/cron.py`
- Modify: `src/codemie/triggers/actors/assistant.py`
- Modify: `src/codemie/triggers/actors/workflow.py`

**Interfaces:**
- Consumes: `SchedulerRun`, `SchedulerRunRepository` from Task 2
- Produces: run records written to DB on every scheduler job execution

- [ ] **Step 1: Write the failing test (append to `test_schedulers.py`)**

```python
from unittest.mock import patch, MagicMock, call
from apscheduler.events import JobExecutionEvent, EVENT_JOB_EXECUTED, EVENT_JOB_SUBMITTED
from codemie.triggers.bindings.cron import Cron


def test_cron_on_job_submitted_creates_run_record():
    cron = Cron()
    event = MagicMock()
    event.code = EVENT_JOB_SUBMITTED
    event.job_id = "sched-1"
    event.scheduled_run_time = MagicMock()

    with patch(
        "codemie.triggers.bindings.cron.SchedulerRunRepository"
    ) as MockRepo:
        mock_run = MagicMock()
        mock_run.id = "run-new"
        MockRepo.return_value.create.return_value = mock_run
        cron._Cron__on_job_event(event)
        MockRepo.return_value.create.assert_called_once()

    assert "sched-1" in cron._active_run_ids
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_cron_on_job_submitted_creates_run_record -v
```

Expected: `AttributeError` — `_active_run_ids` not on `Cron`.

- [ ] **Step 3: Modify `src/codemie/triggers/actors/assistant.py` to return `conversation_id`**

In `invoke_assistant`, find the successful response block and update to return the `conversation_id`:

```python
# Before the try block, conversation_id is already captured as `conversation_id`
# Change the function's final return to:
    logger.info('Successfully invoked assistant: %s, job_id: %s', assistant_id, job_id)
    return {"conversation_id": conversation_id}
```

The function currently returns `None` implicitly. Add `return {"conversation_id": conversation_id}` after the successful `response.raise_for_status()` log line. Keep the existing error handler path returning `None`.

- [ ] **Step 4: Modify `src/codemie/triggers/actors/workflow.py` to return execution ID**

In `invoke_workflow`, after `response.raise_for_status()`, capture and return the execution ID:

```python
        response.raise_for_status()
        exec_id = None
        try:
            exec_id = response.json().get("id")
        except Exception:
            pass
        logger.info('Workflow invoked successfully. job_id: %s, workflow_id: %s', job_id, workflow_id)
        return {"resource_execution_id": exec_id}
```

Keep the existing error handler path; it currently returns `None` implicitly — leave it as-is.

- [ ] **Step 5: Modify `src/codemie/triggers/bindings/cron.py`**

Add `EVENT_JOB_SUBMITTED` and `EVENT_JOB_EXECUTED` to the import:

```python
from apscheduler.events import (
    EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED, EVENT_JOB_SUBMITTED,
)
```

Add `_active_run_ids: dict[str, str]` to `__init__`:

```python
        self._active_run_ids: dict[str, str] = {}
```

Update the listener registration in `start_async` to include the new events:

```python
        self.scheduler.add_listener(
            self.__on_job_event,
            EVENT_JOB_SUBMITTED | EVENT_JOB_EXECUTED | EVENT_JOB_MISSED
            | EVENT_JOB_ERROR | EVENT_JOB_MAX_INSTANCES,
        )
```

Replace `__on_job_event` with the following (keep all existing logging, add persistence):

```python
    def __on_job_event(self, event):
        """Persist run outcomes and report scheduling anomalies."""
        from datetime import datetime, timezone
        from uuid import uuid4
        from codemie.repository.scheduler_run_repository import SchedulerRunRepository
        from codemie.rest_api.models.scheduler_run import SchedulerRun

        repo = SchedulerRunRepository()

        if event.code == EVENT_JOB_SUBMITTED:
            run = SchedulerRun(
                id=str(uuid4()),
                scheduler_id=event.job_id,
                status="running",
                trigger="scheduled",
                started_at=datetime.now(timezone.utc),
            )
            created = repo.create(run)
            self._active_run_ids[event.job_id] = created.id

        elif event.code == EVENT_JOB_EXECUTED:
            run_id = self._active_run_ids.pop(event.job_id, None)
            if run_id:
                finished = datetime.now(timezone.utc)
                retval = event.retval or {}
                fields: dict = {
                    "status": "completed",
                    "finished_at": finished,
                }
                if isinstance(retval, dict):
                    fields["conversation_id"] = retval.get("conversation_id")
                    fields["resource_execution_id"] = retval.get("resource_execution_id")
                    exec_id = retval.get("conversation_id") or retval.get("resource_execution_id")
                    if exec_id:
                        fields["execution_id"] = exec_id
                # Compute duration from the stored started_at
                stored = repo.get_by_id(run_id)
                if stored and stored.started_at:
                    delta = finished - stored.started_at.replace(tzinfo=timezone.utc) \
                        if stored.started_at.tzinfo is None \
                        else finished - stored.started_at
                    fields["duration_ms"] = int(delta.total_seconds() * 1000)
                repo.update(run_id, fields)

        elif event.code == EVENT_JOB_MISSED:
            logger.warning(
                "Scheduled job MISSED - dropped without running: job_id=%s scheduled_for=%s",
                event.job_id,
                event.scheduled_run_time,
            )

        elif event.code == EVENT_JOB_MAX_INSTANCES:
            logger.warning(
                "Scheduled job SKIPPED - previous run still in progress: job_id=%s",
                event.job_id,
            )

        elif event.code == EVENT_JOB_ERROR:
            run_id = self._active_run_ids.pop(event.job_id, None)
            logger.error(
                "Scheduled job FAILED: job_id=%s scheduled_for=%s error=%r",
                event.job_id,
                event.scheduled_run_time,
                event.exception,
            )
            if run_id:
                finished = datetime.now(timezone.utc)
                stored = repo.get_by_id(run_id)
                duration_ms = None
                if stored and stored.started_at:
                    delta = finished - stored.started_at.replace(tzinfo=timezone.utc) \
                        if stored.started_at.tzinfo is None \
                        else finished - stored.started_at
                    duration_ms = int(delta.total_seconds() * 1000)
                repo.update(run_id, {
                    "status": "failed",
                    "finished_at": finished,
                    "duration_ms": duration_ms,
                    "error_data": {
                        "code": type(event.exception).__name__,
                        "message": str(event.exception),
                        "details": repr(event.exception),
                        "timestamp": finished.isoformat(),
                    },
                })
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py::test_cron_on_job_submitted_creates_run_record -v
```

Expected: `1 passed`.

- [ ] **Step 7: Run the full test suite for the schedulers module**

```bash
cd src && poetry run pytest ../tests/codemie/rest_api/routers/test_schedulers.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/codemie/triggers/bindings/cron.py \
        src/codemie/triggers/actors/assistant.py \
        src/codemie/triggers/actors/workflow.py \
        tests/codemie/rest_api/routers/test_schedulers.py
git commit -m "EPMCDME-10682: Persist scheduler run records via APScheduler event callbacks"
```

---

## Task 9: Post-implementation additions (completed during maintenance)

Additional endpoints and logic added after initial implementation:

**`DELETE /v1/scheduler-runs/{run_id}`** — 204 on success, 404 on missing, 409 when status = "running". Implemented in `SchedulerRunService.delete_run` + `SchedulerRunRepository.delete_run`.

**`GET /v1/schedulers/filter-options`** — returns deduped, sorted resources and projects for filter UI. Implemented in `SchedulerSettingsService.get_filter_options`, `_fetch_all_scheduler_settings`, `_build_resource_name_map`. Added `SchedulerFilterOptions` response model.

**Multi-status filtering** — `status` query param on list/stats endpoints accepts comma-separated values (e.g. `completed,failed`), split at the router and translated to `IN (...)` in repository.

**Datasource run tracking** — `_tracked_run_sync` wraps all sync datasource reindex actors. `_extract_datasource_metrics` reads `IndexInfo.tokens_usage` post-run for metrics. All knowledge-base, SVN, and code datasource jobs wired.

**Metric enrichment in `get_run_detail`** — cascade: `run.metrics` → `ConversationMetrics` (assistant) → `WorkflowExecution` (workflow).

**Workflow actor returns metrics** — `invoke_workflow` now returns `resource_execution_id` and `metrics` from response body.

**Additional response fields on `SchedulerRunDetail`** — `workflowExecutionId`, `inputTokens`, `outputTokens`, `executionCost`, `error`.

**`raise_conflict`** — new helper in `routers/utils.py`.

## Final validation

- [x] Run the full scheduler test file
- [x] Run linting (`make ruff`)
- [x] Run existing cron tests to confirm no regression
