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

"""Unit tests for the 79ab1c5725cc migration: the columns that record the time of a manual spend reset."""

import importlib

import pytest


def _module():
    return importlib.import_module("external.alembic.versions.79ab1c5725cc_record_manual_spend_resets")


class _Op:
    def __init__(self):
        self.added: list[tuple[str, str, bool, bool]] = []
        self.dropped: list[tuple[str, str]] = []

    def add_column(self, table, column):
        self.added.append((table, column.name, column.nullable, column.type.timezone))

    def drop_column(self, table, column):
        self.dropped.append((table, column))


@pytest.fixture
def op(monkeypatch) -> _Op:
    fake = _Op()
    monkeypatch.setattr(_module(), "op", fake)
    return fake


def test_upgrade_adds_both_columns(op):
    _module().upgrade()

    assert [(table, name) for table, name, _, _ in op.added] == [
        ("budgets", "spend_reset_at"),
        ("user_budget_assignments", "spend_reset_at"),
    ]


def test_the_columns_are_nullable_timestamps_with_time_zone(op):
    _module().upgrade()

    assert all(nullable and timezone for _, _, nullable, timezone in op.added)


def test_downgrade_drops_both_columns(op):
    _module().downgrade()

    assert op.dropped == [("user_budget_assignments", "spend_reset_at"), ("budgets", "spend_reset_at")]


def test_revises_current_head():
    assert _module().down_revision == "633929ee02fa"
