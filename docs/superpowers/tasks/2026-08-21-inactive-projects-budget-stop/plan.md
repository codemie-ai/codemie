# Inactive Projects Budget Stop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an external service marks a project inactive in `application_enrichment`, a daily cron job stops all project budgets, so spend falls back to each user's personal budget automatically.

**Architecture:** A new `ApplicationEnrichment` table (read-only for CodeMie) flags project activity. Adding `is_active = FALSE` to a `Budget` row causes the existing `BudgetResolutionService` fallback to route spend to global/personal scope — no resolution logic changes needed, only a two-line SQL filter addition. A new `InactiveProjectBudgetStopService` queries inactive enrichment records, flips budgets, emits audit events, and emails project admins. A new `InactiveProjectBudgetScheduler` wraps that service in an APScheduler cron job with `LeaderLockContext` for multi-replica deduplication.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLAlchemy async, APScheduler 3.x, Alembic, aiosmtplib, pytest + pytest-asyncio.

## Global Constraints

- All new `.py` source files must begin with the Apache 2.0 copyright header (first 14 lines identical to every other file in `src/codemie/`).
- All new repositories must be **async** (follow `BudgetRepository`, not `UserEnrichmentRepository` which is sync).
- All background jobs must hold `LeaderLockContext` before doing work.
- `ApplicationEnrichment` uses the **default schema** — no `__table_args__ = {"schema": "codemie"}`.
- `Budget.is_active` migration must use `server_default=sa.text("true")` so existing rows default to active.
- `email_service` must be imported **inside the calling method** (deferred import — see `password_management_service.py`).
- `clear_budget_resolution_cache()` must be called after stopping budgets to evict the 60 s TTL cache.
- `INACTIVE_PROJECT_BUDGET_STOP_ENABLED` defaults to `False` (safe rollout).
- Commit message format: `EPMCDME-13960: <short description>`.

---

## File Map

| Action | Path |
|---|---|
| Modify | `src/codemie/core/models.py` |
| Modify | `src/codemie/service/budget/budget_models.py` |
| Modify | `src/codemie/service/activity/activity_models.py` |
| Modify | `src/codemie/repository/project_budget_repository.py` |
| Modify | `src/codemie/service/budget/budget_resolution_service.py` |
| Modify | `src/codemie/repository/budget_repository.py` |
| Modify | `src/codemie/configs/config.py` |
| Modify | `src/codemie/rest_api/main.py` |
| Create | `src/codemie/repository/application_enrichment_repository.py` |
| Create | `src/codemie/service/budget/inactive_project_budget_stop_service.py` |
| Create | `src/codemie/service/budget/inactive_project_budget_scheduler.py` |
| Create | `src/external/alembic/versions/x1y2z3a4b5c6_add_application_enrichment_table.py` |
| Create | `src/external/alembic/versions/y2z3a4b5c6d7_add_is_active_to_budgets.py` |
| Create | `tests/codemie/repository/test_application_enrichment_repository.py` |
| Create | `tests/codemie/service/budget/test_inactive_project_budget_stop_service.py` |
| Create | `tests/codemie/service/budget/test_inactive_project_budget_scheduler.py` |

---

### Task 1: ApplicationEnrichment model + Alembic migration

**Files:**
- Modify: `src/codemie/core/models.py`
- Create: `src/external/alembic/versions/x1y2z3a4b5c6_add_application_enrichment_table.py`

**Interfaces:**
- Produces: `ApplicationEnrichment` SQLModel class importable as `from codemie.core.models import ApplicationEnrichment`
- Produces: Alembic head `x1y2z3a4b5c6` (down from `w1o2r3k4f5l6`)

- [ ] **Step 1: Write the failing test** (place at bottom of existing test file or create one)

Create `tests/codemie/repository/test_application_enrichment_repository.py` with an import-only smoke test that will fail until the model exists:

```python
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

from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.core.models import ApplicationEnrichment


def test_application_enrichment_model_has_is_active_field():
    e = ApplicationEnrichment(application_id="proj-a", is_active=True)
    assert e.is_active is True
    assert e.application_id == "proj-a"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/repository/test_application_enrichment_repository.py -v
```

Expected: `ImportError` or `AttributeError` — `ApplicationEnrichment` not defined yet.

- [ ] **Step 3: Add ApplicationEnrichment to core/models.py**

Open `src/codemie/core/models.py`. Add these imports if not present (check existing imports at top of file):

```python
from sqlalchemy import Boolean
```

Append the following class **after** the existing `Application` class (do not modify existing code):

```python
class ApplicationEnrichment(SQLModel, table=True):
    """External enrichment data for projects/applications.

    Populated exclusively by an external sync service. CodeMie reads is_active
    to determine whether to stop project budgets via the daily cron job.
    """

    __tablename__ = "application_enrichment"

    application_id: str = Field(
        primary_key=True,
        max_length=100,
        foreign_key="applications.id",
    )
    is_active: bool = Field(
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
        default=True,
    )
    synced_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )
    created_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
        default=None,
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True, onupdate=func.now()),
        default=None,
    )

    __table_args__ = (
        Index("ix_application_enrichment_is_active", "is_active"),
    )
```

Check which imports are already present at the top of `core/models.py` and add any that are missing:
- `from datetime import datetime`
- `from typing import Optional`
- `from sqlalchemy import Boolean, Column, Index, text`
- `from sqlalchemy.dialects.postgresql import TIMESTAMP`
- `from sqlalchemy.sql import func`
- `from sqlmodel import Field, SQLModel`

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/codemie/repository/test_application_enrichment_repository.py::test_application_enrichment_model_has_is_active_field -v
```

Expected: PASS

- [ ] **Step 5: Create Alembic migration for application_enrichment table**

Create `src/external/alembic/versions/x1y2z3a4b5c6_add_application_enrichment_table.py`:

```python
"""add_application_enrichment_table

Revision ID: x1y2z3a4b5c6
Revises: w1o2r3k4f5l6
Create Date: 2026-08-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x1y2z3a4b5c6"
down_revision: Union[str, None] = "w1o2r3k4f5l6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "application_enrichment",
        sa.Column("application_id", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("synced_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name="fk_application_enrichment_application_id",
        ),
        sa.PrimaryKeyConstraint("application_id"),
    )
    op.create_index(
        "ix_application_enrichment_is_active",
        "application_enrichment",
        ["is_active"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_application_enrichment_is_active", table_name="application_enrichment")
    op.drop_table("application_enrichment")
```

- [ ] **Step 6: Commit**

```bash
git add src/codemie/core/models.py \
        src/external/alembic/versions/x1y2z3a4b5c6_add_application_enrichment_table.py \
        tests/codemie/repository/test_application_enrichment_repository.py
git commit -m "EPMCDME-13960: Add ApplicationEnrichment model and migration"
```

---

### Task 2: ApplicationEnrichmentRepository

**Files:**
- Create: `src/codemie/repository/application_enrichment_repository.py`
- Modify: `tests/codemie/repository/test_application_enrichment_repository.py` (add repo tests)

**Interfaces:**
- Produces: `application_enrichment_repository` singleton importable as `from codemie.repository.application_enrichment_repository import application_enrichment_repository`
- Produces: `ApplicationEnrichmentRepository.get_inactive_application_ids(session: AsyncSession) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/repository/test_application_enrichment_repository.py`:

```python
from codemie.repository.application_enrichment_repository import (
    ApplicationEnrichmentRepository,
    application_enrichment_repository,
)


@pytest.mark.asyncio
async def test_get_inactive_application_ids_returns_ids_where_is_active_false():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = ["proj-a", "proj-b"]
    session.execute = AsyncMock(return_value=mock_result)

    repo = ApplicationEnrichmentRepository()
    result = await repo.get_inactive_application_ids(session)

    assert result == ["proj-a", "proj-b"]
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_inactive_application_ids_returns_empty_when_all_active():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=mock_result)

    repo = ApplicationEnrichmentRepository()
    result = await repo.get_inactive_application_ids(session)

    assert result == []


def test_module_singleton_is_repository_instance():
    assert isinstance(application_enrichment_repository, ApplicationEnrichmentRepository)
```

- [ ] **Step 2: Run to verify failure**

```bash
poetry run pytest tests/codemie/repository/test_application_enrichment_repository.py -v
```

Expected: FAIL — `cannot import name 'ApplicationEnrichmentRepository'`

- [ ] **Step 3: Create the repository**

Create `src/codemie/repository/application_enrichment_repository.py`:

```python
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

"""Read-only repository for application enrichment data."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from codemie.core.models import ApplicationEnrichment


class ApplicationEnrichmentRepository:
    """Read-only async repository for ApplicationEnrichment records."""

    async def get_inactive_application_ids(self, session: AsyncSession) -> list[str]:
        """Return application IDs where is_active is False."""
        stmt = select(ApplicationEnrichment.application_id).where(
            ApplicationEnrichment.is_active == False  # noqa: E712
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


application_enrichment_repository = ApplicationEnrichmentRepository()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/repository/test_application_enrichment_repository.py -v
```

Expected: all 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/application_enrichment_repository.py \
        tests/codemie/repository/test_application_enrichment_repository.py
git commit -m "EPMCDME-13960: Add ApplicationEnrichmentRepository"
```

---

### Task 3: Budget.is_active field + migration + SQL filter

**Files:**
- Modify: `src/codemie/service/budget/budget_models.py`
- Create: `src/external/alembic/versions/y2z3a4b5c6d7_add_is_active_to_budgets.py`
- Modify: `src/codemie/repository/project_budget_repository.py`
- Modify: `src/codemie/service/budget/budget_resolution_service.py`
- Modify: `src/codemie/repository/budget_repository.py`

**Interfaces:**
- Produces: `Budget.is_active: bool` field (default `True`)
- Produces: `BudgetRepository.list_active_project_budgets(session, project_name) -> list[Budget]`
- Contract: `get_project_budget_context` and `get_project_budget_categories_batch` return `None`/empty when `b.is_active = FALSE`

Test-first: yes — test that `get_project_budget_context` returns `None` (triggering personal-budget fallback) when we mock a budget row where `is_active = FALSE`.

- [ ] **Step 1: Write failing test for SQL filter behavior**

Create `tests/codemie/repository/test_budget_is_active_filter.py`:

```python
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

from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.budget_repository import BudgetRepository


@pytest.mark.asyncio
async def test_list_active_project_budgets_returns_empty_when_none():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=mock_result)

    repo = BudgetRepository()
    result = await repo.list_active_project_budgets(session, "proj-a")

    assert result == []
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_active_project_budgets_returns_active_budgets():
    from types import SimpleNamespace
    session = AsyncMock()
    budget = SimpleNamespace(budget_id="b-1", project_name="proj-a", is_active=True, deleted_at=None)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [budget]
    session.execute = AsyncMock(return_value=mock_result)

    repo = BudgetRepository()
    result = await repo.list_active_project_budgets(session, "proj-a")

    assert len(result) == 1
    assert result[0].budget_id == "b-1"
```

- [ ] **Step 2: Run to verify failure**

```bash
poetry run pytest tests/codemie/repository/test_budget_is_active_filter.py -v
```

Expected: `AttributeError` — `BudgetRepository has no attribute 'list_active_project_budgets'`

- [ ] **Step 3: Add is_active field to Budget model**

In `src/codemie/service/budget/budget_models.py`, add `Boolean` to the SQLAlchemy imports line:

```python
from sqlalchemy import Boolean, Column, Index, text
```

Add the `is_active` field to the `Budget` class, after the `detached_at` field and before `__table_args__`:

```python
    is_active: bool = Field(
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
        default=True,
    )
```

Add an index entry to `Budget.__table_args__`:

```python
        Index("ix_budgets_is_active", "is_active"),
```

- [ ] **Step 4: Create Alembic migration for is_active on budgets**

Create `src/external/alembic/versions/y2z3a4b5c6d7_add_is_active_to_budgets.py`:

```python
"""add_is_active_to_budgets

Revision ID: y2z3a4b5c6d7
Revises: x1y2z3a4b5c6
Create Date: 2026-08-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y2z3a4b5c6d7"
down_revision: Union[str, None] = "x1y2z3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "budgets",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_index("ix_budgets_is_active", "budgets", ["is_active"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_budgets_is_active", table_name="budgets")
    op.drop_column("budgets", "is_active")
```

- [ ] **Step 5: Add list_active_project_budgets to BudgetRepository**

In `src/codemie/repository/budget_repository.py`, add the method after `list_overdue_reset_budgets` (around line 173):

```python
    async def list_active_project_budgets(
        self, session: AsyncSession, project_name: str
    ) -> list[Budget]:
        """Return all non-deleted, active budgets for a project (all categories)."""
        stmt = select(Budget).where(
            Budget.project_name == project_name,
            Budget.deleted_at.is_(None),
            Budget.is_active == True,  # noqa: E712
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
```

- [ ] **Step 6: Run tests to verify list_active_project_budgets passes**

```bash
poetry run pytest tests/codemie/repository/test_budget_is_active_filter.py -v
```

Expected: both PASS

- [ ] **Step 7: Add AND b.is_active = TRUE filter to get_project_budget_context**

In `src/codemie/repository/project_budget_repository.py`, find `get_project_budget_context` (line ~327) and its raw SQL. Change the WHERE clause:

```sql
-- Before (find this exact text):
              AND  pba.deleted_at IS NULL
            LIMIT 1

-- After:
              AND  pba.deleted_at IS NULL
              AND  b.is_active = TRUE
            LIMIT 1
```

This single change is inside the string assigned to `stmt = text("""...""")` in `get_project_budget_context`.

- [ ] **Step 8: Add AND b.is_active = TRUE filter to get_project_budget_categories_batch**

In the same file, find `get_project_budget_categories_batch` (line ~383). Its WHERE clause ends with:

```sql
-- Before:
              AND  pba.deleted_at IS NULL

-- After:
              AND  pba.deleted_at IS NULL
              AND  b.is_active = TRUE
```

- [ ] **Step 9: Add AND b.is_active = TRUE to BudgetResolutionService.resolve_sync raw SQL**

In `src/codemie/service/budget/budget_resolution_service.py`, find `resolve_sync` (line ~134). Its raw SQL at approximately line ~194 ends with:

```sql
-- Before (find this exact line):
                      AND  pba.budget_category = :budget_category
                      AND  pba.deleted_at IS NULL
                    LIMIT 1

-- After:
                      AND  pba.budget_category = :budget_category
                      AND  pba.deleted_at IS NULL
                      AND  b.is_active = TRUE
                    LIMIT 1
```

- [ ] **Step 10: Run existing budget resolution tests to verify no regression**

```bash
poetry run pytest tests/codemie/service/budget/ tests/codemie/repository/ -v --tb=short
```

Expected: all existing tests PASS; 2 new tests PASS.

- [ ] **Step 11: Commit**

```bash
git add src/codemie/service/budget/budget_models.py \
        src/external/alembic/versions/y2z3a4b5c6d7_add_is_active_to_budgets.py \
        src/codemie/repository/budget_repository.py \
        src/codemie/repository/project_budget_repository.py \
        src/codemie/service/budget/budget_resolution_service.py \
        tests/codemie/repository/test_budget_is_active_filter.py
git commit -m "EPMCDME-13960: Add Budget.is_active field, migration, and SQL filters"
```

---

### Task 4: Activity event constant + InactiveProjectBudgetStopService

**Files:**
- Modify: `src/codemie/service/activity/activity_models.py`
- Create: `src/codemie/service/budget/inactive_project_budget_stop_service.py`
- Create: `tests/codemie/service/budget/test_inactive_project_budget_stop_service.py`

**Interfaces:**
- Consumes: `application_enrichment_repository.get_inactive_application_ids(session) -> list[str]` (Task 2)
- Consumes: `budget_repository.list_active_project_budgets(session, project_name) -> list[Budget]` (Task 3)
- Consumes: `clear_budget_resolution_cache()` from `budget_resolution_service`
- Produces: `inactive_project_budget_stop_service` singleton importable as `from codemie.service.budget.inactive_project_budget_stop_service import inactive_project_budget_stop_service`
- Produces: `InactiveProjectBudgetStopService.run() -> InactiveProjectBudgetStopSummary`
- Produces: `InactiveProjectBudgetStopSummary` dataclass with `stopped: int`, `already_stopped: int`, `notifications_sent: int`, `failed: int`

Test-first: yes — test that `run()` stops budgets for inactive apps, emits events, sends notifications, and calls `clear_budget_resolution_cache()`.

- [ ] **Step 1: Add PROJECT_BUDGET_STOPPED to BudgetManagementEvent**

In `src/codemie/service/activity/activity_models.py`, find the `BudgetManagementEvent` class. Add after the last existing constant (`PROJECT_BUDGET_GROUP_RESET`):

```python
    PROJECT_BUDGET_STOPPED = "budget.project_budget.stopped"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/codemie/service/budget/test_inactive_project_budget_stop_service.py`:

```python
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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from codemie.service.budget.inactive_project_budget_stop_service import (
    InactiveProjectBudgetStopService,
)


def _make_budget(budget_id: str = "b-1", project_name: str = "proj-a") -> SimpleNamespace:
    return SimpleNamespace(
        budget_id=budget_id,
        project_name=project_name,
        is_active=True,
        deleted_at=None,
    )


@pytest.mark.asyncio
async def test_run_stops_budgets_for_inactive_application():
    service = InactiveProjectBudgetStopService()
    budget = _make_budget()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("codemie.service.budget.inactive_project_budget_stop_service.get_async_session", return_value=mock_session),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.application_enrichment_repository.get_inactive_application_ids",
            new=AsyncMock(return_value=["proj-a"]),
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.budget_repository.list_active_project_budgets",
            new=AsyncMock(return_value=[budget]),
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.activity_event_repository.async_insert",
            new=AsyncMock(),
        ) as mock_insert,
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.clear_budget_resolution_cache",
        ) as mock_clear_cache,
        patch.object(service, "_notify_project_admins", new=AsyncMock()),
    ):
        summary = await service.run()

    assert budget.is_active is False
    mock_session.add.assert_called_once_with(budget)
    mock_session.commit.assert_awaited_once()
    mock_insert.assert_awaited_once()
    mock_clear_cache.assert_called_once()
    assert summary.stopped == 1
    assert summary.notifications_sent == 1
    assert summary.failed == 0


@pytest.mark.asyncio
async def test_run_skips_when_no_inactive_applications():
    service = InactiveProjectBudgetStopService()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("codemie.service.budget.inactive_project_budget_stop_service.get_async_session", return_value=mock_session),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.application_enrichment_repository.get_inactive_application_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.clear_budget_resolution_cache",
        ) as mock_clear_cache,
    ):
        summary = await service.run()

    mock_session.commit.assert_not_awaited()
    mock_clear_cache.assert_not_called()
    assert summary.stopped == 0
    assert summary.notifications_sent == 0


@pytest.mark.asyncio
async def test_run_counts_already_stopped_when_no_active_budgets():
    service = InactiveProjectBudgetStopService()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("codemie.service.budget.inactive_project_budget_stop_service.get_async_session", return_value=mock_session),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.application_enrichment_repository.get_inactive_application_ids",
            new=AsyncMock(return_value=["proj-a"]),
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.budget_repository.list_active_project_budgets",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.clear_budget_resolution_cache",
        ) as mock_clear_cache,
    ):
        summary = await service.run()

    assert summary.stopped == 0
    assert summary.already_stopped == 1
    mock_clear_cache.assert_called_once()


@pytest.mark.asyncio
async def test_run_increments_failed_on_exception():
    service = InactiveProjectBudgetStopService()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("codemie.service.budget.inactive_project_budget_stop_service.get_async_session", return_value=mock_session),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.application_enrichment_repository.get_inactive_application_ids",
            new=AsyncMock(return_value=["proj-a"]),
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.budget_repository.list_active_project_budgets",
            new=AsyncMock(side_effect=RuntimeError("db error")),
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.clear_budget_resolution_cache",
        ),
    ):
        summary = await service.run()

    assert summary.failed == 1
    assert summary.stopped == 0
```

- [ ] **Step 3: Run to verify failure**

```bash
poetry run pytest tests/codemie/service/budget/test_inactive_project_budget_stop_service.py -v
```

Expected: `ImportError` — module does not exist yet.

- [ ] **Step 4: Create InactiveProjectBudgetStopService**

Create `src/codemie/service/budget/inactive_project_budget_stop_service.py`:

```python
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

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from codemie.clients.postgres import get_async_session
from codemie.configs import logger
from codemie.repository.application_enrichment_repository import application_enrichment_repository
from codemie.repository.budget_repository import budget_repository
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    BudgetManagementEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.budget.budget_resolution_service import clear_budget_resolution_cache
from codemie.rest_api.models.user_management import UserDB, UserProject


@dataclass
class InactiveProjectBudgetStopSummary:
    stopped: int = 0
    already_stopped: int = 0
    notifications_sent: int = 0
    failed: int = 0


class InactiveProjectBudgetStopService:
    """Stop all active project budgets for applications marked inactive in ApplicationEnrichment.

    Called by the daily cron job. Setting Budget.is_active = False causes
    BudgetResolutionService to fall back to personal/global budget automatically
    (the existing fallback chain returns None → global scope → no provider dispatch).
    """

    async def run(self) -> InactiveProjectBudgetStopSummary:
        summary = InactiveProjectBudgetStopSummary()

        async with get_async_session() as session:
            inactive_app_ids = await application_enrichment_repository.get_inactive_application_ids(session)
            if not inactive_app_ids:
                logger.info(
                    "component=inactive_project_budget_stop_service event=no_inactive_applications"
                )
                return summary

            for app_id in inactive_app_ids:
                try:
                    budgets = await budget_repository.list_active_project_budgets(session, app_id)
                    if not budgets:
                        summary.already_stopped += 1
                        continue

                    for budget in budgets:
                        budget.is_active = False
                        session.add(budget)
                        await activity_event_repository.async_insert(
                            ActivityEventCreate(
                                domain=ActivityDomain.BUDGET_MANAGEMENT,
                                event_type=BudgetManagementEvent.PROJECT_BUDGET_STOPPED,
                                entity_type=ActivityEntityType.BUDGET,
                                entity_id=budget.budget_id,
                                attributes={"project_name": app_id},
                            ),
                            session,
                        )
                        logger.info(
                            f"component=inactive_project_budget_stop_service event=budget_stopped "
                            f"project_name={app_id!r} budget_id={budget.budget_id!r}"
                        )

                    await self._notify_project_admins(session, app_id)
                    summary.stopped += len(budgets)
                    summary.notifications_sent += 1

                except Exception as exc:
                    summary.failed += 1
                    logger.error(
                        f"component=inactive_project_budget_stop_service event=project_stop_failed "
                        f"project_name={app_id!r} error={exc}",
                        exc_info=True,
                    )

            await session.commit()

        clear_budget_resolution_cache()
        logger.info(
            f"component=inactive_project_budget_stop_service event=run_completed "
            f"stopped={summary.stopped} already_stopped={summary.already_stopped} "
            f"notifications_sent={summary.notifications_sent} failed={summary.failed}"
        )
        return summary

    async def _notify_project_admins(self, session: AsyncSession, project_name: str) -> None:
        stmt = (
            select(UserDB.email)
            .join(UserProject, UserProject.user_id == UserDB.id)
            .where(
                UserProject.project_name == project_name,
                UserProject.is_project_admin == True,  # noqa: E712
                UserDB.is_active == True,  # noqa: E712
                UserDB.deleted_at.is_(None),
            )
        )
        result = await session.execute(stmt)
        admin_emails = list(result.scalars().all())

        if not admin_emails:
            logger.info(
                f"component=inactive_project_budget_stop_service event=no_admins_to_notify "
                f"project_name={project_name!r}"
            )
            return

        from codemie.service.email_service import email_service

        subject = f"Budget stopped for project {project_name}"
        html_body = (
            f"<p>The budget for project <strong>{project_name}</strong> has been stopped "
            "because the project is inactive. All requests will now be charged to your "
            "personal budget.</p>"
        )
        for email in admin_emails:
            try:
                await email_service.send_email(to=email, subject=subject, html_body=html_body)
                logger.info(
                    f"component=inactive_project_budget_stop_service event=notification_sent "
                    f"project_name={project_name!r}"
                )
            except Exception as exc:
                logger.error(
                    f"component=inactive_project_budget_stop_service event=notification_failed "
                    f"project_name={project_name!r} error={exc}",
                    exc_info=True,
                )


inactive_project_budget_stop_service = InactiveProjectBudgetStopService()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/budget/test_inactive_project_budget_stop_service.py -v
```

Expected: all 4 PASS

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/activity/activity_models.py \
        src/codemie/service/budget/inactive_project_budget_stop_service.py \
        tests/codemie/service/budget/test_inactive_project_budget_stop_service.py
git commit -m "EPMCDME-13960: Add InactiveProjectBudgetStopService with audit events and email notifications"
```

---

### Task 5: Config + Scheduler + main.py wiring

**Files:**
- Modify: `src/codemie/configs/config.py`
- Create: `src/codemie/service/budget/inactive_project_budget_scheduler.py`
- Modify: `src/codemie/rest_api/main.py`
- Create: `tests/codemie/service/budget/test_inactive_project_budget_scheduler.py`

**Interfaces:**
- Consumes: `inactive_project_budget_stop_service.run()` (Task 4)
- Produces: `InactiveProjectBudgetScheduler` class with `start()` and `stop()` methods
- Produces: `config.INACTIVE_PROJECT_BUDGET_STOP_ENABLED: bool` (default `False`)
- Produces: `config.INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE: str` (default `"0 0 * * *"`)

Test-first: yes — test `_run_inactive_project_budget_stop` skips when not leader, and runs service when leader.

- [ ] **Step 1: Write failing scheduler tests**

Create `tests/codemie/service/budget/test_inactive_project_budget_scheduler.py`:

```python
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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.budget.inactive_project_budget_scheduler import InactiveProjectBudgetScheduler


@pytest.mark.asyncio
async def test_run_skips_when_not_leader():
    scheduler = InactiveProjectBudgetScheduler(scheduler=MagicMock())
    mock_lock = SimpleNamespace(acquired=False)

    with (
        patch(
            "codemie.service.budget.inactive_project_budget_scheduler.LeaderLockContext",
            return_value=MagicMock(__enter__=MagicMock(return_value=mock_lock), __exit__=MagicMock()),
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_scheduler.inactive_project_budget_stop_service.run",
            new=AsyncMock(),
        ) as mock_run,
    ):
        await scheduler._run_inactive_project_budget_stop()

    mock_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_calls_service_when_leader():
    from codemie.service.budget.inactive_project_budget_stop_service import InactiveProjectBudgetStopSummary
    scheduler = InactiveProjectBudgetScheduler(scheduler=MagicMock())
    mock_lock = SimpleNamespace(acquired=True)
    mock_summary = InactiveProjectBudgetStopSummary(stopped=2, notifications_sent=1)

    with (
        patch(
            "codemie.service.budget.inactive_project_budget_scheduler.LeaderLockContext",
            return_value=MagicMock(__enter__=MagicMock(return_value=mock_lock), __exit__=MagicMock()),
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_scheduler.inactive_project_budget_stop_service.run",
            new=AsyncMock(return_value=mock_summary),
        ) as mock_run,
    ):
        await scheduler._run_inactive_project_budget_stop()

    mock_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_logs_error_on_exception_and_does_not_raise():
    scheduler = InactiveProjectBudgetScheduler(scheduler=MagicMock())
    mock_lock = SimpleNamespace(acquired=True)

    with (
        patch(
            "codemie.service.budget.inactive_project_budget_scheduler.LeaderLockContext",
            return_value=MagicMock(__enter__=MagicMock(return_value=mock_lock), __exit__=MagicMock()),
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_scheduler.inactive_project_budget_stop_service.run",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        # Must not raise — scheduler wrapper catches all exceptions
        await scheduler._run_inactive_project_budget_stop()
```

- [ ] **Step 2: Run to verify failure**

```bash
poetry run pytest tests/codemie/service/budget/test_inactive_project_budget_scheduler.py -v
```

Expected: `ImportError` — `InactiveProjectBudgetScheduler` does not exist yet.

- [ ] **Step 3: Add config vars**

In `src/codemie/configs/config.py`, find the block containing `LITELLM_SPEND_COLLECTOR_ENABLED` (line ~745). Add directly after `LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE`:

```python
    INACTIVE_PROJECT_BUDGET_STOP_ENABLED: bool = False  # Enables the inactive project budget stop APScheduler job
    INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE: str = "0 0 * * *"  # Daily at midnight UTC
```

- [ ] **Step 4: Create InactiveProjectBudgetScheduler**

Create `src/codemie/service/budget/inactive_project_budget_scheduler.py`:

```python
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

from apscheduler.triggers.cron import CronTrigger

from codemie.configs import config, logger
from codemie.service.budget.inactive_project_budget_stop_service import (
    inactive_project_budget_stop_service,
)
from codemie.utils.leader_lock import LeaderLockContext

_INACTIVE_PROJECT_BUDGET_STOP_LOCK_ID = 987654325


def _build_cron_trigger(cron_expression: str) -> CronTrigger | None:
    """Return a UTC CronTrigger or None when the expression is invalid."""
    parts = cron_expression.split()
    if len(parts) != 5:
        return None
    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone="UTC",
    )


class InactiveProjectBudgetScheduler:
    """Scheduler for the inactive-project budget stop cron job."""

    def __init__(self, scheduler) -> None:
        self.scheduler = scheduler

    def start(self) -> None:
        """Register the job and start the scheduler."""
        self._register_inactive_project_budget_stop_job()
        if not self.scheduler.running:
            self.scheduler.start()

    def _register_inactive_project_budget_stop_job(self) -> None:
        trigger = _build_cron_trigger(config.INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE)
        if trigger is None:
            logger.error(
                f"Invalid INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE cron expression: "
                f"{config.INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE!r}; skipping job registration"
            )
            return
        self.scheduler.add_job(
            self._run_inactive_project_budget_stop,
            trigger=trigger,
            id="inactive_project_budget_stop",
            replace_existing=True,
            name="Inactive Project Budget Stop",
        )
        logger.info(
            f"Registered inactive project budget stop job with schedule: "
            f"{config.INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE!r} (UTC)"
        )

    async def _run_inactive_project_budget_stop(self) -> None:
        """Job wrapper: acquire leader lock then run the stop service."""
        with LeaderLockContext(lock_id=_INACTIVE_PROJECT_BUDGET_STOP_LOCK_ID) as lock:
            if not lock.acquired:
                logger.info("Inactive project budget stop: not the leader, skipping")
                return
            try:
                summary = await inactive_project_budget_stop_service.run()
                logger.info(
                    f"Inactive project budget stop completed: stopped={summary.stopped} "
                    f"already_stopped={summary.already_stopped} "
                    f"notifications_sent={summary.notifications_sent} "
                    f"failed={summary.failed}"
                )
            except Exception as exc:
                logger.error(f"Inactive project budget stop failed: {exc}", exc_info=True)

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("InactiveProjectBudgetScheduler stopped")
```

- [ ] **Step 5: Run scheduler tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/budget/test_inactive_project_budget_scheduler.py -v
```

Expected: all 3 PASS

- [ ] **Step 6: Wire scheduler into main.py**

In `src/codemie/rest_api/main.py`, add the following function after `_setup_spend_tracking_scheduler` (around line 420, before `_setup_leaderboard_scheduler`):

```python
def _setup_inactive_project_budget_scheduler(app: FastAPI):
    """Setup inactive project budget stop scheduler if enabled."""
    if not config.INACTIVE_PROJECT_BUDGET_STOP_ENABLED:
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.budget.inactive_project_budget_scheduler import InactiveProjectBudgetScheduler

    scheduler_instance = AsyncIOScheduler()
    scheduler = InactiveProjectBudgetScheduler(scheduler=scheduler_instance)
    scheduler.start()
    app.state.inactive_project_budget_scheduler = scheduler
    logger.info("Inactive project budget scheduler started successfully")
```

Then in the `lifespan` function, add the call after `_setup_spend_tracking_scheduler(app)` (around line 750):

```python
    _setup_inactive_project_budget_scheduler(app)
```

- [ ] **Step 7: Run full budget test suite**

```bash
poetry run pytest tests/codemie/service/budget/ tests/codemie/repository/ -v --tb=short
```

Expected: all tests PASS (no regressions).

- [ ] **Step 8: Commit**

```bash
git add src/codemie/configs/config.py \
        src/codemie/service/budget/inactive_project_budget_scheduler.py \
        src/codemie/rest_api/main.py \
        tests/codemie/service/budget/test_inactive_project_budget_scheduler.py
git commit -m "EPMCDME-13960: Add InactiveProjectBudgetScheduler and config, wire into main.py"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task |
|---|---|
| `application_enrichment` table with FK to `applications` and `is_active` | Task 1 |
| Table populated by external service (CodeMie reads only) | Task 1 (read-only repo in Task 2) |
| `is_active` column on `Budget` | Task 3 |
| Cron job every 24 h | Task 5 |
| Check inactive projects from enrichment table | Task 4 (`run()`) |
| Stop budget if exists | Task 4 (set `is_active=False` on all budgets) |
| Notify project admins budget is stopped | Task 4 (`_notify_project_admins`) |
| Stopped project cannot receive spend / redirect to personal budget | Task 3 (SQL filter makes resolution return global scope) |

**All requirements covered. No placeholders detected.**
