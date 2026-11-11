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

"""Unit tests for the f7c3a9d1e4b6 orphan user_projects cleanup migration."""

from __future__ import annotations

import logging
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

import pytest
from alembic.script import ScriptDirectory


def _module() -> ModuleType:
    from external.alembic.versions import f7c3a9d1e4b6_delete_orphan_user_projects as module

    return module


def _normalize(statement: Any) -> str:
    return " ".join(str(statement).split())


# --- _classify ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exact_match", "any_case_match", "expected"),
    [(True, True, "exact"), (False, True, "case_only"), (False, False, "orphan")],
)
def test_classify(exact_match: bool, any_case_match: bool, expected: str) -> None:
    assert _module()._classify(exact_match, any_case_match) == expected


# --- upgrade() / downgrade() against a mocked op ------------------------------------------------

# (id, user_id, project_name, is_default, exact_match, any_case_match)
Row = tuple[str, str, str, bool, bool, bool]
# (statement, normalized sql, params)
Call = tuple[Any, str, dict[str, Any] | None]

EXACT: Row = ("exact", "user-1", "alpha", False, True, True)
CASE_ONLY: Row = ("case_only", "user-2", "MyTeam", False, False, True)
ORPHAN_DEFAULT: Row = ("orphan1", "user-3", "gone", True, False, False)
ORPHAN_PERSONAL: Row = ("orphan2", "user-4", "old@x.com", False, False, False)
ROWS = [EXACT, CASE_ONLY, ORPHAN_DEFAULT, ORPHAN_PERSONAL]


class _ScriptedBind:
    def __init__(self, rows: list[Row], rowcount: int | None = None) -> None:
        self.rows = rows
        self.rowcount = rowcount
        self.calls: list[Call] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> mock.MagicMock:
        sql = _normalize(statement)
        self.calls.append((statement, sql, params))
        result = mock.MagicMock()
        if sql.startswith("SELECT"):
            result.fetchall.return_value = self.rows
        elif sql.startswith("DELETE"):
            result.rowcount = len(params["ids"]) if self.rowcount is None else self.rowcount
        return result

    def sql_calls(self, prefix: str) -> list[Call]:
        return [(statement, sql, params) for statement, sql, params in self.calls if sql.startswith(prefix)]


def _run(fn_name: str, bind: _ScriptedBind) -> mock.MagicMock:
    op = mock.MagicMock()
    op.get_bind.return_value = bind
    with mock.patch.object(_module(), "op", op):
        getattr(_module(), fn_name)()
    return op


def _upgrade(rows: list[Row]) -> _ScriptedBind:
    bind = _ScriptedBind(rows)
    _run("upgrade", bind)
    return bind


def _messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def _summaries(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [m for m in _messages(caplog, logging.INFO) if "scanned" in m]


def test_upgrade_deletes_only_orphans_and_logs_each(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)

    bind = _upgrade(ROWS)

    [(_, _, params)] = bind.sql_calls("DELETE")
    assert params["ids"] == ["orphan1", "orphan2"]

    deleted = [m for m in _messages(caplog, logging.INFO) if "Deleting orphan" in m]
    assert len(deleted) == 2
    assert "'user-3'" in deleted[0] and "'gone'" in deleted[0] and "is_default=True" in deleted[0]
    assert "'user-4'" in deleted[1] and "'old@x.com'" in deleted[1] and "is_default=False" in deleted[1]


def test_upgrade_logs_one_summary_line(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)

    _upgrade(ROWS)

    assert _summaries(caplog) == [
        "Orphan user_projects cleanup: scanned=4 exact_kept=1 case_only_kept=1 orphans_deleted=2"
    ]


@pytest.mark.parametrize(
    ("rows", "expected_summary"),
    [
        (
            [EXACT, CASE_ONLY],
            "Orphan user_projects cleanup: scanned=2 exact_kept=1 case_only_kept=1 orphans_deleted=0",
        ),
        ([], "Orphan user_projects cleanup: scanned=0 exact_kept=0 case_only_kept=0 orphans_deleted=0"),
    ],
)
def test_upgrade_without_orphans_issues_no_delete(
    caplog: pytest.LogCaptureFixture, rows: list[Row], expected_summary: str
) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)

    bind = _upgrade(rows)

    assert bind.sql_calls("DELETE") == []
    assert _summaries(caplog) == [expected_summary]


def test_upgrade_reports_rows_actually_deleted_by_the_recheck(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    bind = _ScriptedBind(ROWS, rowcount=1)

    _run("upgrade", bind)

    [(_, _, params)] = bind.sql_calls("DELETE")
    assert params["ids"] == ["orphan1", "orphan2"]
    assert _summaries(caplog) == [
        "Orphan user_projects cleanup: scanned=4 exact_kept=1 case_only_kept=1 orphans_deleted=1"
    ]


# Both sides of the comparison must be lowered, or case-only rows are deleted irreversibly.
_ANY_CASE_KEYS = (
    "(SELECT LOWER(id) FROM codemie.applications "
    "UNION SELECT LOWER(name) FROM codemie.applications WHERE name IS NOT NULL)"
)


def test_select_matches_id_and_name_case_insensitively() -> None:
    sql = _normalize(_module()._SELECT_ROWS)

    assert f"LOWER(up.project_name) IN {_ANY_CASE_KEYS} AS any_case_match" in sql
    assert "codemie.applications" in sql
    assert "name IS NOT NULL" in sql
    assert "deleted_at" not in sql
    assert "LIKE" not in sql


def test_delete_rechecks_no_match() -> None:
    sql = _normalize(_module()._DELETE_ORPHANS)

    assert "id = ANY(:ids)" in sql
    assert f"AND LOWER(up.project_name) NOT IN {_ANY_CASE_KEYS}" in sql
    assert "name IS NOT NULL" in sql
    assert "deleted_at" not in sql


def test_downgrade_changes_no_data(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    bind = _ScriptedBind([])

    op = _run("downgrade", bind)

    assert bind.calls == []
    assert op.execute.call_count == 0
    infos = _messages(caplog, logging.INFO)
    assert len(infos) == 1 and "intentionally not reverted" in infos[0]


def test_cleanup_revision_is_in_the_single_alembic_head_chain() -> None:
    script = ScriptDirectory(str(Path(__file__).resolve().parents[3] / "src" / "external" / "alembic"))

    heads = script.get_heads()
    assert len(heads) == 1
    assert "f7c3a9d1e4b6" in {rev.revision for rev in script.iterate_revisions(heads[0], "base")}
    assert script.get_revision("f7c3a9d1e4b6").down_revision == "f8d24a6c0b17"
