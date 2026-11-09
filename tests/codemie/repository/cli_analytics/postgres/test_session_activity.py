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

"""The invariant the stored activity facts rely on (EPMCDME-16427): who writes what they are derived from."""

from __future__ import annotations

import re

from codemie.repository.cli_analytics.postgres import reader, session_binding, session_derived
from codemie.repository.cli_analytics.postgres.rollups import RECOMPUTE
from codemie.repository.cli_analytics.postgres.session_activity import ACTIVITY_TABLES


def test_the_session_binding_never_writes_the_start_or_a_day_row() -> None:
    # It runs on ingest, outside the refresher, so nothing derives the facts after it: it must not change their inputs.
    written = " ".join((session_binding._BIND_SQL, session_binding._TOUCH_BOUND_SQL))

    assert "started_at" not in written
    assert "started_at" not in session_binding._BINDING_COLUMNS
    for table in ACTIVITY_TABLES:
        assert table not in written


def test_the_other_session_dims_writers_leave_the_start_alone() -> None:
    assert "started_at" not in " ".join((session_derived._WRITE_FEATURE_ID, session_derived._WRITE_DERIVED))


def test_the_start_and_the_day_rows_are_written_by_the_refresher_statements() -> None:
    # The statements the refresher runs before it derives the facts are the ones that write the start and the day rows.
    statements = " ".join(RECOMPUTE)

    assert re.search(r"INSERT INTO session_dims AS sd \(.*started_at", statements, re.S)
    for table in ACTIVITY_TABLES:
        assert f"INSERT INTO {table}" in statements, table


def test_the_activity_tables_of_the_stored_facts_are_the_ones_of_the_window_rule() -> None:
    # session_activity keeps its own copy (the two modules do not import each other): the facts stand in for the rule's
    # probes of these tables only while the tuples are equal, so a change to one must fail here until the other follows
    assert ACTIVITY_TABLES == reader._ACTIVITY_TABLES
