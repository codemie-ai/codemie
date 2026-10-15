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

"""Unit tests for the e5f9a1c3b7d2 MS Teams settings repair migration."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy.dialects.postgresql import JSONB


def _module() -> ModuleType:
    from external.alembic.versions import e5f9a1c3b7d2_repair_ms_teams_migrated_settings as module

    return module


ASSISTANTS = {"own": ("p1", False), "glob": ("p2", True), "for": ("p2", False)}


# --- _decode_credential_values ------------------------------------------------------------------


def test_decode_credential_values_decodes_json_string_array() -> None:
    raw = '[{"key": "assistant_ids", "value": ["a1"]}]'
    assert _module()._decode_credential_values(raw) == [{"key": "assistant_ids", "value": ["a1"]}]


def test_decode_credential_values_passes_list_through() -> None:
    raw = [{"key": "assistant_ids", "value": ["a1"]}]
    assert _module()._decode_credential_values(raw) == raw


@pytest.mark.parametrize("raw", ['"x"', "{}", "not json"])
def test_decode_credential_values_returns_none_for_non_array(raw: str) -> None:
    assert _module()._decode_credential_values(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        '[{"key": "assistant_ids", "value": []}, "x"]',
        '[{"key": "assistant_ids", "value": []}, 5]',
        "[null]",
        [{"key": "assistant_ids", "value": []}, "x"],
    ],
)
def test_decode_credential_values_returns_none_for_non_dict_element(raw: object) -> None:
    assert _module()._decode_credential_values(raw) is None


# --- _filter_assistant_ids ----------------------------------------------------------------------


def test_filter_assistant_ids_keeps_owned_and_global_and_reports_drops() -> None:
    values = [
        {"key": "webhook", "value": "w"},
        {"key": "assistant_ids", "value": ["own", "glob", "for", "gone", 5, "own"]},
    ]

    new_values, dropped = _module()._filter_assistant_ids(values, "p1", ASSISTANTS)

    assert new_values == [{"key": "webhook", "value": "w"}, {"key": "assistant_ids", "value": ["own", "glob"]}]
    assert dropped == [("for", "foreign"), ("gone", "deleted"), (5, "invalid"), ("own", "invalid")]


def test_filter_assistant_ids_keeps_empty_entry_when_all_dropped() -> None:
    values = [{"key": "assistant_ids", "value": ["for", "gone"]}]

    new_values, dropped = _module()._filter_assistant_ids(values, "p1", ASSISTANTS)

    assert new_values == [{"key": "assistant_ids", "value": []}]
    assert dropped == [("for", "foreign"), ("gone", "deleted")]


def test_filter_assistant_ids_adds_empty_entry_when_missing() -> None:
    values = [{"key": "webhook", "value": "w"}]

    new_values, dropped = _module()._filter_assistant_ids(values, "p1", ASSISTANTS)

    assert new_values == [{"key": "webhook", "value": "w"}, {"key": "assistant_ids", "value": []}]
    assert dropped == []


def test_filter_assistant_ids_replaces_non_list_value_with_empty_list() -> None:
    values = [{"key": "assistant_ids", "value": "own"}]

    new_values, _ = _module()._filter_assistant_ids(values, "p1", ASSISTANTS)

    assert new_values == [{"key": "assistant_ids", "value": []}]


def test_filter_assistant_ids_does_not_mutate_input() -> None:
    values = [{"key": "assistant_ids", "value": ["own", "gone"]}]

    _module()._filter_assistant_ids(values, "p1", ASSISTANTS)

    assert values == [{"key": "assistant_ids", "value": ["own", "gone"]}]


# --- _build_alias -------------------------------------------------------------------------------


def test_build_alias_formats_row_date_to_minutes() -> None:
    alias = _module()._build_alias(datetime(2026, 9, 7, 8, 0, 45), set(), datetime(2026, 1, 1))
    assert alias == "msteams-2026-09-07_08-00"


def test_build_alias_suffixes_on_collision() -> None:
    alias = _module()._build_alias(datetime(2026, 9, 7, 8, 0, 45), {"msteams-2026-09-07_08-00"}, datetime(2026, 1, 1))
    assert alias == "msteams-2026-09-07_08-00-2"


def test_build_alias_uses_now_when_date_is_null() -> None:
    alias = _module()._build_alias(None, set(), datetime(2026, 10, 15, 13, 5, 59))
    assert alias == "msteams-2026-10-15_13-05"


# --- upgrade() / downgrade() against a mocked op ------------------------------------------------

ROW_DATE = datetime(2026, 9, 7, 8, 0, 45)
ALIAS = "msteams-2026-09-07_08-00"

# (id, project_name, setting_type, user_id, alias, date, credential_values, jsonb_typeof)
Row = tuple[str, str, str, str | None, str | None, datetime | None, str | list[dict], str]
# (id, project, is_global)
AssistantRow = tuple[str, str | None, bool | None]
# (statement, normalized sql, params)
Call = tuple[Any, str, dict[str, Any] | None]


def _row(
    row_id: str,
    values: str | list[dict],
    *,
    project: str = "p1",
    setting_type: str = "PROJECT",
    user_id: str | None = None,
    alias: str | None = None,
    date: datetime | None = ROW_DATE,
) -> Row:
    """A targeted settings row; a str ``values`` is a jsonb string, a list a jsonb array."""
    values_type = "string" if isinstance(values, str) else "array"
    return (row_id, project, setting_type, user_id, alias, date, values, values_type)


def _string_values(ids: list[object]) -> str:
    return json.dumps([{"key": "assistant_ids", "value": ids}])


class _ScriptedBind:
    def __init__(
        self,
        rows: list[Row],
        assistants: Sequence[AssistantRow] = (),
        aliases: dict[str, list[str]] | None = None,
    ) -> None:
        self.rows = rows
        self.assistants = list(assistants)
        self.aliases = aliases or {}
        self.calls: list[Call] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> mock.MagicMock:
        sql = " ".join(str(statement).split())
        self.calls.append((statement, sql, params))
        result = mock.MagicMock()
        if "FROM codemie.assistants" in sql:
            result.fetchall.return_value = [a for a in self.assistants if a[0] in params["ids"]]
        elif sql.startswith("SELECT alias"):
            result.fetchall.return_value = [(a,) for a in self.aliases.get(params["project_name"], [])]
        elif sql.startswith("SELECT"):
            result.fetchall.return_value = self.rows
        return result

    def sql_calls(self, prefix: str) -> list[Call]:
        return [(statement, sql, params) for statement, sql, params in self.calls if sql.startswith(prefix)]

    def values_updates(self) -> list[Call]:
        return [c for c in self.sql_calls("UPDATE") if "SET credential_values" in c[1]]

    def alias_updates(self) -> list[Call]:
        return [c for c in self.sql_calls("UPDATE") if "SET alias" in c[1]]


def _run(fn_name: str, bind: _ScriptedBind) -> mock.MagicMock:
    op = mock.MagicMock()
    op.get_bind.return_value = bind
    with mock.patch.object(_module(), "op", op):
        getattr(_module(), fn_name)()
    return op


def _upgrade(
    rows: list[Row],
    assistants: Sequence[AssistantRow] = (),
    aliases: dict[str, list[str]] | None = None,
) -> _ScriptedBind:
    bind = _ScriptedBind(rows, assistants, aliases)
    _run("upgrade", bind)
    return bind


def _messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def test_upgrade_targets_only_broken_ms_teams_rows() -> None:
    bind = _upgrade([])

    _, sql, _ = bind.calls[0]
    assert "credential_type = 'MS_TEAMS'" in sql
    assert "jsonb_typeof(credential_values) = 'string'" in sql
    assert "alias IS NULL OR alias = ''" in sql
    assert len(bind.calls) == 1


def test_upgrade_decodes_filters_and_aliases_string_row(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    bind = _upgrade(
        [_row("s1", _string_values(["own", "for", "gone"]))],
        assistants=[("own", "p1", False), ("for", "p2", False)],
    )

    [(statement, _, params)] = bind.values_updates()
    assert isinstance(params["credential_values"], list)
    assert params["credential_values"] == [{"key": "assistant_ids", "value": ["own"]}]
    assert isinstance(statement._bindparams["credential_values"].type, JSONB)
    assert params["id"] == "s1"

    [(_, _, alias_params)] = bind.alias_updates()
    assert alias_params == {"id": "s1", "alias": ALIAS}

    drops = [m for m in _messages(caplog, logging.INFO) if "Dropped" in m]
    assert len(drops) == 2
    assert all("'s1'" in m and "'p1'" in m for m in drops)
    assert any("'for'" in m and "foreign" in m for m in drops)
    assert any("'gone'" in m and "deleted" in m for m in drops)


def test_upgrade_leaves_existing_alias_of_string_row_untouched() -> None:
    bind = _upgrade([_row("s1", _string_values(["own"]), alias="my-teams")], assistants=[("own", "p1", False)])

    assert len(bind.values_updates()) == 1
    assert bind.alias_updates() == []


def test_upgrade_only_aliases_array_row() -> None:
    bind = _upgrade([_row("a1", [{"key": "assistant_ids", "value": ["x"]}])])

    assert bind.values_updates() == []
    assert bind.sql_calls("SELECT id, project, is_global") == []
    [(_, _, params)] = bind.alias_updates()
    assert params == {"id": "a1", "alias": ALIAS}


def test_upgrade_treats_empty_alias_as_missing() -> None:
    bind = _upgrade([_row("a1", [{"key": "assistant_ids", "value": []}], alias="")])

    assert len(bind.alias_updates()) == 1


def test_upgrade_skips_row_that_does_not_decode_to_array(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    bind = _upgrade([_row("bad", json.dumps({"key": "x"})), _row("ok", _string_values([]), alias="kept")])

    warnings = _messages(caplog, logging.WARNING)
    assert len(warnings) == 1 and "'bad'" in warnings[0]
    updated_ids = [params["id"] for _, _, params in bind.sql_calls("UPDATE")]
    assert updated_ids == ["ok"]


def test_upgrade_skips_string_row_with_non_dict_element(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    raw = json.dumps([{"key": "assistant_ids", "value": ["own"]}, "x"])

    bind = _upgrade([_row("bad", raw, alias="kept")], assistants=[("own", "p1", False)])

    warnings = _messages(caplog, logging.WARNING)
    assert len(warnings) == 1 and "'bad'" in warnings[0]
    assert bind.sql_calls("UPDATE") == []


def test_upgrade_suffixes_alias_assigned_earlier_in_the_same_run() -> None:
    bind = _upgrade([_row("a1", []), _row("a2", [])])

    aliases = [params["alias"] for _, _, params in bind.alias_updates()]
    assert aliases == [ALIAS, f"{ALIAS}-2"]


def test_upgrade_suffixes_alias_taken_by_another_setting_in_the_project() -> None:
    bind = _upgrade([_row("a1", [])], aliases={"p1": [ALIAS]})

    [(_, _, params)] = bind.alias_updates()
    assert params["alias"] == f"{ALIAS}-2"


def test_upgrade_scopes_user_row_alias_lookup_to_user_id() -> None:
    bind = _upgrade([_row("u1", [], setting_type="USER", user_id="user-1")])

    [(_, sql, params)] = bind.sql_calls("SELECT alias")
    assert "user_id IS NOT DISTINCT FROM :user_id" in sql
    assert params == {"project_name": "p1", "user_id": "user-1"}


def test_upgrade_project_row_alias_lookup_is_project_wide() -> None:
    bind = _upgrade([_row("a1", [])])

    [(_, sql, params)] = bind.sql_calls("SELECT alias")
    assert "user_id" not in sql
    assert params == {"project_name": "p1"}


def test_upgrade_uses_run_time_when_row_date_is_null() -> None:
    with mock.patch.object(_module(), "datetime") as fake_datetime:
        fake_datetime.now.return_value = datetime(2026, 10, 15, 13, 5, 59)
        bind = _upgrade([_row("a1", [], date=None)])

    [(_, _, params)] = bind.alias_updates()
    assert params["alias"] == "msteams-2026-10-15_13-05"


def test_upgrade_logs_one_summary_line(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_module().logger.name)
    _upgrade(
        [
            _row("s1", _string_values(["own", "gone"])),
            _row("a1", []),
            _row("bad", '"x"', alias="kept"),
        ],
        assistants=[("own", "p1", False)],
    )

    summaries = [m for m in _messages(caplog, logging.INFO) if "scanned" in m]
    assert summaries == ["MS Teams settings repair: scanned=3 decoded=1 aliased=2 ids_dropped=1 skipped=1"]


def test_downgrade_changes_no_data() -> None:
    bind = _ScriptedBind([])
    op = _run("downgrade", bind)

    assert bind.calls == []
    assert op.execute.call_count == 0


def test_repair_revision_is_in_the_single_alembic_head_chain() -> None:
    script = ScriptDirectory(str(Path(__file__).resolve().parents[3] / "src" / "external" / "alembic"))

    heads = script.get_heads()
    assert len(heads) == 1
    assert "e5f9a1c3b7d2" in {rev.revision for rev in script.iterate_revisions(heads[0], "base")}
    assert script.get_revision("e5f9a1c3b7d2").down_revision == "y1z2a3b4c5d6"
