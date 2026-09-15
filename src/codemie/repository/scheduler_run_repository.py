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

from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import text
from sqlmodel import Session

from codemie.clients.postgres import PostgresClient
from codemie.rest_api.models.scheduler_run import SchedulerRun


class SchedulerRunRepository:
    """Data access for scheduler run records."""

    @staticmethod
    def _get_engine():
        return PostgresClient.get_engine()

    def create(self, run: SchedulerRun) -> SchedulerRun:
        with Session(self._get_engine()) as session:
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def update(self, run_id: str, fields: dict[str, Any]) -> None:
        with Session(self._get_engine()) as session:
            run = session.get(SchedulerRun, run_id)
            if run is None:
                return
            for key, value in fields.items():
                setattr(run, key, value)
            session.add(run)
            session.commit()

    def get_by_id(self, run_id: str) -> Optional[SchedulerRun]:
        with Session(self._get_engine()) as session:
            run = session.get(SchedulerRun, run_id)
            if run is not None:
                # Eagerly load JSONB columns before session closes
                _ = run.logs, run.input_data, run.result_data, run.error_data, run.metrics
            return run

    def list_runs(
        self,
        scheduler_id: Optional[str] = None,
        status: Optional[List[str]] = None,
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
            placeholders = ", ".join(f":status_{i}" for i in range(len(status)))
            conditions.append(f"sr.status IN ({placeholders})")
            for i, s in enumerate(status):
                params[f"status_{i}"] = s
        if date_from:
            conditions.append("sr.started_at >= :date_from")
            params["date_from"] = date_from
        if date_to:
            conditions.append("sr.started_at <= :date_to")
            params["date_to"] = date_to
        if search:
            conditions.append(
                "(sr.execution_id ILIKE :search OR sr.conversation_id ILIKE :search "
                "OR sr.resource_execution_id ILIKE :search)"
            )
            params["search"] = f"%{search}%"

        where = "WHERE " + " AND ".join(conditions)
        order = "ASC" if sort_direction.lower() == "asc" else "DESC"
        # CR-002: keep count_params separate from row_params to avoid unknown bind-param error
        count_params = dict(params)
        row_params = {**params, "limit": per_page, "offset": page * per_page}

        count_sql = text(f"SELECT COUNT(*) FROM codemie.scheduler_runs sr {where}")
        rows_sql = text(
            f"SELECT * FROM codemie.scheduler_runs sr {where} "
            f"ORDER BY sr.started_at {order} "
            f"LIMIT :limit OFFSET :offset"
        )

        # CR-001: use self._get_engine() — SchedulerRun has no get_engine() class method
        with self._get_engine().connect() as conn:
            total = conn.execute(count_sql.bindparams(**count_params)).scalar_one()
            rows = conn.execute(rows_sql.bindparams(**row_params)).mappings().all()

        runs = [SchedulerRun(**dict(row)) for row in rows]
        return runs, total

    def get_stats(
        self,
        scheduler_id: Optional[str] = None,
        status: Optional[List[str]] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> dict[str, Any]:
        conditions = ["sr.scheduler_id IS NOT NULL"]
        params: dict[str, Any] = {}

        if scheduler_id:
            conditions.append("sr.scheduler_id = :scheduler_id")
            params["scheduler_id"] = scheduler_id
        # CR-004: apply status, resource_type, resource_id filters
        if status:
            placeholders = ", ".join(f":status_{i}" for i in range(len(status)))
            conditions.append(f"sr.status IN ({placeholders})")
            for i, s in enumerate(status):
                params[f"status_{i}"] = s
        # resource_type and resource_id live in settings.credential_values JSONB — require join
        if resource_type:
            conditions.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(s.credential_values::jsonb) cv "
                "WHERE cv->>'key' = 'resource_type' AND lower(cv->>'value') = lower(:resource_type))"
            )
            params["resource_type"] = resource_type
        if resource_id:
            conditions.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(s.credential_values::jsonb) cv "
                "WHERE cv->>'key' = 'resource_id' AND cv->>'value' = :resource_id)"
            )
            params["resource_id"] = resource_id
        if date_from:
            conditions.append("sr.started_at >= :date_from")
            params["date_from"] = date_from
        if date_to:
            conditions.append("sr.started_at <= :date_to")
            params["date_to"] = date_to

        settings_join = "LEFT JOIN codemie.settings s ON s.id = sr.scheduler_id"
        where = "WHERE " + " AND ".join(conditions)

        sql = text(
            f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE sr.status = 'completed') AS completed,
                COUNT(*) FILTER (WHERE sr.status = 'failed') AS failed,
                COUNT(*) FILTER (WHERE sr.status = 'running') AS running,
                COUNT(*) FILTER (WHERE sr.status = 'cancelled') AS cancelled,
                AVG(sr.duration_ms) FILTER (WHERE sr.duration_ms IS NOT NULL
                    AND sr.status IN ('completed', 'failed')) AS avg_duration_ms
            FROM codemie.scheduler_runs sr
            {settings_join}
            {where}
            """
        )

        # CR-001: use self._get_engine() — SchedulerRun has no get_engine() class method
        with self._get_engine().connect() as conn:
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

    def delete_run(self, run_id: str) -> str | None:
        """Atomically delete a non-running run. Returns 'not_found' or 'running' on guard failure."""
        with self._get_engine().connect() as conn:
            result = conn.execute(
                text("DELETE FROM codemie.scheduler_runs WHERE id = :run_id AND status != 'running'"),
                {"run_id": run_id},
            )
            conn.commit()
            if result.rowcount == 0:
                # Distinguish not-found from status-conflict
                exists = conn.execute(
                    text("SELECT 1 FROM codemie.scheduler_runs WHERE id = :run_id"),
                    {"run_id": run_id},
                ).scalar()
                return "running" if exists else "not_found"
        return None

    def get_logs_page(self, run_id: str, page: int = 0, per_page: int = 50) -> tuple[list[dict[str, Any]], int]:
        run = self.get_by_id(run_id)
        if run is None or not run.logs:
            return [], 0
        total = len(run.logs)
        start = page * per_page
        return run.logs[start : start + per_page], total
