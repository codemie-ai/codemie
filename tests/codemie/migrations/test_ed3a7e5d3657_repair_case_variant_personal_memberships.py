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

"""Unit tests for the ed3a7e5d3657 case-variant personal membership repair (EPMCDME-16566)."""

from __future__ import annotations

import logging
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

import pytest
from alembic.script import ScriptDirectory


def _module() -> ModuleType:
    from external.alembic.versions import ed3a7e5d3657_repair_case_variant_personal_memberships as module

    return module


def _normalize(statement: Any) -> str:
    return " ".join(str(statement).split())


# (id, user_id, project_name, is_default, app_name, project_type, created_by, app_deleted, has_real_row)
Row = tuple[str, str, str, bool, str, str, str | None, bool, bool]
# (normalized sql, params)
Call = tuple[str, dict[str, Any] | None]

REAL = "Mixed.Case@epam.com"
ORPHAN = "mixed.case@epam.com"

WITH_REAL_ROW: Row = ("o1", "user-1", ORPHAN, False, REAL, "personal", "user-1", False, True)
DEFAULT_WITH_REAL_ROW: Row = (
    "o2",
    "user-2",
    "other.user@epam.com",
    True,
    "Other.User@epam.com",
    "personal",
    "user-2",
    False,
    True,
)
WITHOUT_REAL_ROW: Row = ("o3", "user-3", "third@epam.com", True, "Third@epam.com", "personal", "user-3", False, False)
NOT_PERSONAL: Row = ("o4", "user-4", "myteam", False, "MyTeam", "shared", "user-4", False, False)
OTHER_OWNER: Row = ("o5", "user-5", "someone@epam.com", False, "Someone@epam.com", "personal", "user-6", False, False)
DELETED_APP: Row = ("o6", "user-7", "gone@epam.com", False, "Gone@epam.com", "personal", "user-7", True, False)
ROWS = [WITH_REAL_ROW, DEFAULT_WITH_REAL_ROW, WITHOUT_REAL_ROW, NOT_PERSONAL, OTHER_OWNER, DELETED_APP]


class _ScriptedBind:
    """Returns ROWS for the SELECT and `rowcount` (default 1) for every DELETE/UPDATE."""

    def __init__(self, rows: list[Row], rowcounts: dict[str, int] | None = None) -> None:
        self.rows = rows
        self.rowcounts = rowcounts or {}
        self.calls: list[Call] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> mock.MagicMock:
        sql = _normalize(statement)
        self.calls.append((sql, params))
        result = mock.MagicMock()
        if sql.startswith("SELECT"):
            result.fetchall.return_value = self.rows
        else:
            key = (params or {}).get("id") or (params or {}).get("user_id")
            result.rowcount = self.rowcounts.get(f"{sql.split()[0]}:{key}", 1)
        return result

    def sql_calls(self, prefix: str) -> list[Call]:
        return [(sql, params) for sql, params in self.calls if sql.startswith(prefix)]


def _run(fn_name: str, bind: _ScriptedBind) -> mock.MagicMock:
    op = mock.MagicMock()
    op.get_bind.return_value = bind
    with mock.patch.object(_module(), "op", op):
        getattr(_module(), fn_name)()
    return op


def _summaries(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "scanned=" in r.getMessage()]


# --- _classify ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (WITH_REAL_ROW, "delete"),
        (DEFAULT_WITH_REAL_ROW, "delete"),
        (WITHOUT_REAL_ROW, "rename"),
        (NOT_PERSONAL, "not_personal"),
        (OTHER_OWNER, "other_owner"),
        (DELETED_APP, "deleted_project"),
    ],
)
def test_classify(row: Row, expected: str) -> None:
    _, user_id, _, _, _, project_type, created_by, app_deleted, has_real_row = row
    assert _module()._classify(user_id, project_type, created_by, app_deleted, has_real_row) == expected


# --- upgrade() ----------------------------------------------------------------------------------


def test_upgrade_deletes_orphans_that_have_the_real_membership() -> None:
    bind = _ScriptedBind(ROWS)

    _run("upgrade", bind)

    deleted = [params["id"] for _, params in bind.sql_calls("DELETE")]
    assert deleted == ["o1", "o2"]


def test_upgrade_moves_the_default_from_a_deleted_orphan_to_the_real_row() -> None:
    bind = _ScriptedBind(ROWS)

    _run("upgrade", bind)

    defaults = [params for sql, params in bind.sql_calls("UPDATE") if "is_default = true" in sql]
    assert defaults == [{"user_id": "user-2", "app_name": "Other.User@epam.com"}]


def test_upgrade_renames_orphans_without_the_real_membership() -> None:
    bind = _ScriptedBind(ROWS)

    _run("upgrade", bind)

    renames = [params for sql, params in bind.sql_calls("UPDATE") if "SET project_name" in sql]
    assert renames == [{"id": "o3", "app_name": "Third@epam.com"}]


def test_upgrade_keeps_rows_of_shared_foreign_or_deleted_projects() -> None:
    bind = _ScriptedBind([NOT_PERSONAL, OTHER_OWNER, DELETED_APP])

    _run("upgrade", bind)

    assert [sql for sql, _ in bind.calls if not sql.startswith("SELECT")] == []


def test_upgrade_logs_one_summary_with_counts(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)

    _run("upgrade", _ScriptedBind(ROWS))

    assert _summaries(caplog) == [
        "Case-variant personal membership repair: scanned=6 deleted=2 renamed=1 defaults_moved=1 "
        "kept_not_personal=1 kept_other_owner=1 kept_deleted_project=1"
    ]


def test_upgrade_deletes_a_row_whose_rename_lost_to_a_real_row_created_meanwhile(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    bind = _ScriptedBind([WITHOUT_REAL_ROW], rowcounts={"UPDATE:o3": 0})

    _run("upgrade", bind)

    assert [params["id"] for _, params in bind.sql_calls("DELETE")] == ["o3"]
    assert _summaries(caplog) == [
        "Case-variant personal membership repair: scanned=1 deleted=1 renamed=0 defaults_moved=1 "
        "kept_not_personal=0 kept_other_owner=0 kept_deleted_project=0"
    ]


def test_upgrade_counts_only_rows_the_recheck_actually_deleted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    bind = _ScriptedBind([WITH_REAL_ROW], rowcounts={"DELETE:o1": 0})

    _run("upgrade", bind)

    assert _summaries(caplog) == [
        "Case-variant personal membership repair: scanned=1 deleted=0 renamed=0 defaults_moved=0 "
        "kept_not_personal=0 kept_other_owner=0 kept_deleted_project=0"
    ]


def test_upgrade_with_no_rows_changes_nothing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    bind = _ScriptedBind([])

    _run("upgrade", bind)

    assert len(bind.calls) == 1
    assert _summaries(caplog) == [
        "Case-variant personal membership repair: scanned=0 deleted=0 renamed=0 defaults_moved=0 "
        "kept_not_personal=0 kept_other_owner=0 kept_deleted_project=0"
    ]


# --- SQL shape ----------------------------------------------------------------------------------


def test_select_takes_only_rows_that_match_an_application_by_casing_alone() -> None:
    sql = _normalize(_module()._SELECT_CASE_VARIANTS)

    assert "JOIN codemie.applications a ON LOWER(a.name) = LOWER(up.project_name)" in sql
    assert (
        "up.project_name NOT IN (SELECT id FROM codemie.applications "
        "UNION SELECT name FROM codemie.applications WHERE name IS NOT NULL)"
    ) in sql
    assert "LIKE" not in sql


def test_delete_rechecks_that_the_real_membership_exists() -> None:
    sql = _normalize(_module()._DELETE_ORPHAN)

    assert "up.id = :id" in sql
    assert (
        "EXISTS (SELECT 1 FROM codemie.user_projects r WHERE r.user_id = up.user_id AND r.project_name = :app_name)"
        in sql
    )


def test_rename_rechecks_that_no_real_membership_exists() -> None:
    sql = _normalize(_module()._RENAME_ORPHAN)

    assert "SET project_name = :app_name" in sql
    assert (
        "NOT EXISTS (SELECT 1 FROM codemie.user_projects r WHERE r.user_id = up.user_id AND r.project_name = :app_name)"
        in sql
    )


def test_default_move_never_creates_a_second_default() -> None:
    sql = _normalize(_module()._MOVE_DEFAULT)

    assert "SET is_default = true" in sql
    assert "NOT EXISTS (SELECT 1 FROM codemie.user_projects d WHERE d.user_id = :user_id AND d.is_default)" in sql


# --- downgrade() and chain ----------------------------------------------------------------------


def test_downgrade_changes_no_data(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    bind = _ScriptedBind([])

    op = _run("downgrade", bind)

    assert bind.calls == []
    assert op.execute.call_count == 0


def test_repair_revision_is_in_the_single_alembic_head_chain() -> None:
    script = ScriptDirectory(str(Path(__file__).resolve().parents[3] / "src" / "external" / "alembic"))

    heads = script.get_heads()
    assert len(heads) == 1
    assert "ed3a7e5d3657" in {rev.revision for rev in script.iterate_revisions(heads[0], "base")}
    assert script.get_revision("ed3a7e5d3657").down_revision == "c8d2f6a1b9e3"
