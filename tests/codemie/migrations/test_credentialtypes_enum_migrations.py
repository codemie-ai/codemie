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

"""Consistency checks for the hand-written ``credentialtypes`` enum lists in Alembic migrations.

Every migration that changes the enum re-declares the full ordered value list by hand. These tests
replay each migration's ``upgrade()``/``downgrade()`` against a mocked ``op`` and check that:

* the enum left by the head revision matches the ``CredentialTypes`` model, and
* each migration's ``downgrade()`` restores exactly the enum left by its nearest ancestor that
  also changed it — so a re-parented or hand-edited list that drops or misorders a value fails CI.
"""

import logging
from functools import cache
from pathlib import Path
from unittest import mock

import pytest
from alembic.script import ScriptDirectory

from codemie_tools.base.models import CredentialTypes

ENUM_NAME = "credentialtypes"
ALEMBIC_DIR = Path(__file__).resolve().parents[3] / "src" / "external" / "alembic"

# Historical migrations whose downgrade() restores the parent's values in a different order. The
# reorder is harmless (the enum is rebuilt either way) and these revisions are already applied
# everywhere, so only set equality is checked for them. Do not add new revisions here.
_ORDER_ONLY_DOWNGRADES = {"fa14587c0de1", "8b2c1a4d5e6f", "6cce754bc484"}


@cache
def _script() -> ScriptDirectory:
    return ScriptDirectory(str(ALEMBIC_DIR))


def _enum_values(revision, fn_name: str) -> list[str] | None:
    """Run ``upgrade``/``downgrade`` with a mocked ``op`` and return the last credentialtypes list."""
    op = mock.MagicMock()
    with mock.patch.object(revision.module, "op", op):
        getattr(revision.module, fn_name)()
    calls = [
        c.kwargs["new_values"] for c in op.sync_enum_values.call_args_list if c.kwargs.get("enum_name") == ENUM_NAME
    ]
    return calls[-1] if calls else None


@cache
def _enum_migrations() -> dict[str, tuple[list[str], list[str]]]:
    """Map revision id -> (values after upgrade, values after downgrade) for enum-changing migrations."""
    result = {}
    for revision in _script().walk_revisions():
        source = Path(revision.path).read_text()
        if ENUM_NAME in source and "sync_enum_values" in source:
            result[revision.revision] = (_enum_values(revision, "upgrade"), _enum_values(revision, "downgrade"))
    return result


def _ancestors(revision_id: str) -> set[str]:
    seen: set[str] = set()
    frontier = list(_script().get_revision(revision_id)._all_down_revisions)
    while frontier:
        current = frontier.pop()
        if current not in seen:
            seen.add(current)
            frontier.extend(_script().get_revision(current)._all_down_revisions)
    return seen


def _nearest_enum_ancestors(revision_id: str) -> set[str]:
    """Enum-changing ancestors that are not themselves ancestors of another enum-changing ancestor."""
    candidates = _ancestors(revision_id) & _enum_migrations().keys()
    return {c for c in candidates if not any(c in _ancestors(other) for other in candidates - {c})}


def test_head_enum_matches_credential_types_model():
    heads = _script().get_heads()
    assert len(heads) == 1, f"expected a single Alembic head, got {heads}"
    latest = {heads[0]} if heads[0] in _enum_migrations() else _nearest_enum_ancestors(heads[0])
    assert len(latest) == 1, f"parallel enum-changing revisions {latest} below the head"

    assert _enum_migrations()[latest.pop()][0] == [member.name for member in CredentialTypes]


@pytest.mark.parametrize("revision_id", sorted(_enum_migrations()))
def test_downgrade_restores_parent_enum(revision_id):
    parents = _nearest_enum_ancestors(revision_id)
    if not parents:
        pytest.skip("first migration that syncs the enum; the enum was created by create_table")
    assert len(parents) == 1, f"{revision_id} has parallel enum-changing ancestors {parents}"

    parent_values = _enum_migrations()[parents.pop()][0]
    downgrade_values = _enum_migrations()[revision_id][1]
    if revision_id in _ORDER_ONLY_DOWNGRADES:
        assert set(downgrade_values) == set(parent_values)
    else:
        assert downgrade_values == parent_values


class TestRemoveZephyrSquadMigration:
    REVISION = "d9e8f7a6b5c4"

    def _run_upgrade(self, rowcount: int) -> mock.MagicMock:
        revision = _script().get_revision(self.REVISION)
        op = mock.MagicMock()
        op.get_bind.return_value.execute.return_value.rowcount = rowcount
        with mock.patch.object(revision.module, "op", op):
            revision.module.upgrade()
        return op

    def test_upgrade_drops_only_zephyr_squad(self):
        upgrade_values, downgrade_values = _enum_migrations()[self.REVISION]

        assert "ZEPHYR_SQUAD" not in upgrade_values
        assert [v for v in downgrade_values if v != "ZEPHYR_SQUAD"] == upgrade_values

    def test_upgrade_deletes_rows_before_dropping_enum_label(self):
        op = self._run_upgrade(rowcount=0)

        statement = op.get_bind.return_value.execute.call_args.args[0]
        assert "DELETE FROM codemie.settings WHERE credential_type::text = 'ZEPHYR_SQUAD'" in str(statement)
        assert [c[0] for c in op.mock_calls if c[0] in ("get_bind", "sync_enum_values")] == [
            "get_bind",
            "sync_enum_values",
        ]

    def test_upgrade_logs_info_when_no_rows_deleted(self, caplog):
        with caplog.at_level(logging.INFO):
            self._run_upgrade(rowcount=0)

        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.INFO, "No ZEPHYR_SQUAD settings rows to delete")
        ]

    def test_upgrade_logs_warning_with_count_when_rows_deleted(self, caplog):
        with caplog.at_level(logging.INFO):
            self._run_upgrade(rowcount=3)

        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.WARNING, "Deleted 3 ZEPHYR_SQUAD settings rows (0 expected)")
        ]
