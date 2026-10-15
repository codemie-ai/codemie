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

"""Unit tests for c3d4e5f6a7b8 migration transform logic."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from unittest import mock

import pytest


def _import_transforms():
    from external.alembic.versions.c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings import (
        _group_assistant_ids,
        _merge_assistant_ids,
    )

    return _group_assistant_ids, _merge_assistant_ids


def test_group_assistant_ids_dedups_within_project():
    _group_assistant_ids, _ = _import_transforms()
    rows = [("proj1", "a1"), ("proj1", "a2"), ("proj1", "a1")]
    result = _group_assistant_ids(rows)
    assert result == {"proj1": ["a1", "a2"]}


def test_group_assistant_ids_splits_by_project_preserving_order():
    _group_assistant_ids, _ = _import_transforms()
    rows = [("proj1", "a1"), ("proj2", "b1"), ("proj1", "a2"), ("proj2", "b2")]
    result = _group_assistant_ids(rows)
    assert result == {"proj1": ["a1", "a2"], "proj2": ["b1", "b2"]}


def test_group_assistant_ids_empty_rows():
    _group_assistant_ids, _ = _import_transforms()
    assert _group_assistant_ids([]) == {}


def test_merge_assistant_ids_appends_new_and_dedupes():
    _, _merge_assistant_ids = _import_transforms()
    existing = [{"key": "assistant_ids", "value": ["a1", "a2"]}]
    result = _merge_assistant_ids(existing, ["a2", "a3"])
    assert result == [{"key": "assistant_ids", "value": ["a1", "a2", "a3"]}]


def test_merge_assistant_ids_preserves_other_credential_entries():
    _, _merge_assistant_ids = _import_transforms()
    existing = [
        {"key": "webhook_url", "value": "https://example.test"},
        {"key": "assistant_ids", "value": ["a1"]},
    ]
    result = _merge_assistant_ids(existing, ["a2"])
    assert result[0] == {"key": "webhook_url", "value": "https://example.test"}
    assert result[1] == {"key": "assistant_ids", "value": ["a1", "a2"]}


def test_merge_assistant_ids_adds_entry_when_missing():
    _, _merge_assistant_ids = _import_transforms()
    existing = [{"key": "webhook_url", "value": "https://example.test"}]
    result = _merge_assistant_ids(existing, ["a1"])
    assert {"key": "assistant_ids", "value": ["a1"]} in result
    assert len(result) == 2


def test_merge_assistant_ids_does_not_mutate_input():
    _, _merge_assistant_ids = _import_transforms()
    existing = [{"key": "assistant_ids", "value": ["a1"]}]
    _merge_assistant_ids(existing, ["a2"])
    assert existing == [{"key": "assistant_ids", "value": ["a1"]}]


# --- upgrade() against a mocked op -------------------------------------------------------------

ALIAS_PATTERN = re.compile(r"^msteams-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(-\d+)?$")


def _module():
    from external.alembic.versions import c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings as module

    return module


def _run_upgrade(
    mapping_rows: list[tuple[str, str]],
    existing_by_project: dict[str, tuple[str, list[dict]]],
    aliases_by_project: dict[str, list[str]],
    assistants: list[tuple[str, str | None, bool | None]] | None = None,
) -> list[tuple[str, dict | None]]:
    """Run upgrade() with a scripted bind; return the list of (sql, params) write calls.

    assistants are the codemie.assistants rows (id, project, is_global); by default every
    mapped assistant exists and belongs to the project it is mapped to.
    """
    if assistants is None:
        assistants = [(assistant_id, project_name, False) for project_name, assistant_id in mapping_rows]
    writes = []

    def execute(statement: object, params: dict | None = None) -> mock.MagicMock:
        sql = str(statement)
        result = mock.MagicMock()
        if "FROM codemie.assistants" in sql:
            result.fetchall.return_value = [a for a in assistants if a[0] in params["ids"]]
        elif "FROM assistant_project_mapping" in sql:
            result.fetchall.return_value = mapping_rows
        elif sql.startswith("SELECT id, credential_values"):
            result.fetchone.return_value = existing_by_project.get(params["project_name"])
        elif sql.startswith("SELECT alias"):
            result.fetchall.return_value = [(a,) for a in aliases_by_project.get(params["project_name"], [])]
        else:
            writes.append((sql, params))
        return result

    op = mock.MagicMock()
    op.get_bind.return_value.execute.side_effect = execute
    with mock.patch.object(_module(), "op", op):
        _module().upgrade()
    return writes


def test_upgrade_binds_credential_values_as_list_on_insert_and_merge():
    writes = _run_upgrade(
        mapping_rows=[("new_proj", "a1"), ("old_proj", "b1")],
        existing_by_project={"old_proj": ("row-1", [{"key": "assistant_ids", "value": ["b0"]}])},
        aliases_by_project={},
    )

    inserts = [p for sql, p in writes if sql.startswith("INSERT")]
    updates = [p for sql, p in writes if sql.startswith("UPDATE")]
    assert len(inserts) == 1 and len(updates) == 1
    assert inserts[0]["credential_values"] == [{"key": "assistant_ids", "value": ["a1"]}]
    assert updates[0]["credential_values"] == [{"key": "assistant_ids", "value": ["b0", "b1"]}]


def test_upgrade_inserts_non_null_msteams_alias():
    writes = _run_upgrade(mapping_rows=[("new_proj", "a1")], existing_by_project={}, aliases_by_project={})

    sql, params = next((sql, p) for sql, p in writes if sql.startswith("INSERT"))
    assert ":alias" in sql
    assert ALIAS_PATTERN.match(params["alias"])


def test_upgrade_suffixes_alias_colliding_with_project_alias():
    fixed_now = datetime(2026, 9, 7, 8, 0, 45)
    with mock.patch.object(_module(), "datetime") as fake_datetime:
        fake_datetime.now.return_value = fixed_now
        writes = _run_upgrade(
            mapping_rows=[("new_proj", "a1")],
            existing_by_project={},
            aliases_by_project={"new_proj": ["msteams-2026-09-07_08-00"]},
        )

    params = next(p for sql, p in writes if sql.startswith("INSERT"))
    assert params["alias"] == "msteams-2026-09-07_08-00-2"


def test_filter_owned_assistant_ids_keeps_owned_and_global_and_reports_drops() -> None:
    assistants = {"own": ("p1", False), "glob": ("p2", True), "for": ("p2", False)}

    kept, dropped = _module()._filter_owned_assistant_ids(["own", "glob", "for", "gone"], "p1", assistants)

    assert kept == ["own", "glob"]
    assert dropped == [("for", "foreign"), ("gone", "deleted")]


def test_upgrade_drops_foreign_and_deleted_ids_before_insert_and_merge(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    writes = _run_upgrade(
        mapping_rows=[
            ("new_proj", "a1"),
            ("new_proj", "x1"),
            ("old_proj", "b1"),
            ("old_proj", "g1"),
            ("old_proj", "d1"),
        ],
        existing_by_project={"old_proj": ("row-1", [{"key": "assistant_ids", "value": ["b0"]}])},
        aliases_by_project={},
        assistants=[
            ("a1", "new_proj", False),
            ("x1", "other", False),
            ("b1", "old_proj", False),
            ("g1", "other", True),
        ],
    )

    inserts = [p for sql, p in writes if sql.startswith("INSERT")]
    updates = [p for sql, p in writes if sql.startswith("UPDATE")]
    assert inserts[0]["credential_values"] == [{"key": "assistant_ids", "value": ["a1"]}]
    assert updates[0]["credential_values"] == [{"key": "assistant_ids", "value": ["b0", "b1", "g1"]}]

    drops = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "Dropped" in r.getMessage()]
    assert len(drops) == 2
    assert any("'x1'" in m and "'new_proj'" in m and "foreign" in m for m in drops)
    assert any("'d1'" in m and "'old_proj'" in m and "deleted" in m for m in drops)


def test_upgrade_inserts_empty_assistant_ids_when_every_id_is_dropped() -> None:
    writes = _run_upgrade(
        mapping_rows=[("new_proj", "x1")],
        existing_by_project={},
        aliases_by_project={},
        assistants=[("x1", "other", False)],
    )

    [params] = [p for sql, p in writes if sql.startswith("INSERT")]
    assert params["credential_values"] == [{"key": "assistant_ids", "value": []}]


def test_build_alias_uses_now_when_date_is_null():
    alias = _module()._build_alias(None, set(), datetime(2026, 10, 15, 13, 5, 59))
    assert alias == "msteams-2026-10-15_13-05"


def test_build_alias_skips_taken_suffixes():
    taken = {"msteams-2026-09-07_08-00", "msteams-2026-09-07_08-00-2"}
    alias = _module()._build_alias(datetime(2026, 9, 7, 8, 0, 45), taken, datetime(2026, 1, 1))
    assert alias == "msteams-2026-09-07_08-00-3"
