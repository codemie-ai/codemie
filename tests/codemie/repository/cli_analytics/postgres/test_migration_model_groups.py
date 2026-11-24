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

"""Revision a1c1a0000013: the stored token groups of a session (EPMCDME-17051), without a database.

Step 1 creates the table `session_model_groups`, the nullable stamp `session_dims.model_groups_rev` and two control
columns on `session_sort_keys_control`, under a bounded lock wait. Step 2 builds the partial index of the sessions whose
groups are not trusted yet: plain on a small `session_dims`, CONCURRENTLY outside any transaction above 150,000 rows.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codemie.repository.cli_analytics.postgres import migrations
from tests.codemie.repository.cli_analytics.postgres.test_migrations import SORT_KEYS, _load_revision, _normalized

GROUPS = _load_revision("a1c1a0000013_cli_analytics_session_model_groups.py")
INDEX = "session_dims_model_groups_pending"


def test_the_model_groups_are_one_revision_after_the_sort_keys_with_one_head() -> None:
    assert GROUPS.revision == "a1c1a0000013"
    assert GROUPS.down_revision == SORT_KEYS.revision == "a1c1a0000012"

    revisions = {}
    for path in (Path(migrations.ALEMBIC_DIR) / "versions").glob("a1c1a*.py"):
        module = _load_revision(path.name)
        revisions[module.revision] = module.down_revision
    heads = set(revisions) - {down for down in revisions.values() if down}
    assert heads == {"a1c1a0000022"}


def test_the_column_step_bounds_the_lock_wait_and_is_idempotent() -> None:
    statements = [_normalized(s) for s in GROUPS.column_statements()]

    assert statements[0] == "SET LOCAL lock_timeout = '5s'"
    assert sum(s.startswith("SET LOCAL lock_timeout") for s in statements) == 1
    assert all("IF NOT EXISTS" in s for s in statements[1:])
    assert len(statements) == 5  # the table, the stamp column, the two control columns


def test_the_table_holds_the_token_sums_of_one_session_and_raw_model() -> None:
    create = _normalized(next(s for s in GROUPS.column_statements() if "CREATE TABLE" in s))

    assert create.startswith("CREATE TABLE IF NOT EXISTS session_model_groups (")
    for column in (
        "session_id text NOT NULL",
        "model_name text NOT NULL",
        "normalized_model_name text NOT NULL",
        "input_tokens bigint NOT NULL",
        "output_tokens bigint NOT NULL",
        "cache_read_tokens bigint NOT NULL",
        "cache_creation_tokens bigint NOT NULL",
        "has_cost_daily boolean NOT NULL",
    ):
        assert column in create
    # the split sums stay NULL when no row carries a split, as model_token_groups yields them
    assert re.search(r"cache_creation_5m_tokens bigint(,|\))", create)
    assert re.search(r"cache_creation_1h_tokens bigint(,|\))", create)
    assert "PRIMARY KEY (session_id, model_name)" in create
    assert create.endswith("WITH (fillfactor = 90)")
    assert not re.search(r"cost|rate", create.replace("has_cost_daily", ""))  # tokens only: nothing is priced here


def test_the_stamp_and_the_control_columns_have_the_types_the_readers_expect() -> None:
    statements = [_normalized(s) for s in GROUPS.column_statements()]

    assert any("ALTER TABLE session_dims ADD COLUMN model_groups_rev smallint;" in s for s in statements)
    assert not any("model_groups_rev smallint " in s for s in statements)  # NULL, no default
    for column in ("model_groups_enabled", "model_groups_operator_disabled"):
        assert any(
            f"ALTER TABLE session_sort_keys_control ADD COLUMN {column} boolean NOT NULL DEFAULT false;" in s
            for s in statements
        )


def test_a_column_that_exists_is_not_altered() -> None:
    statements = [_normalized(s) for s in GROUPS.column_statements()]

    alters = [s for s in statements if "ALTER TABLE" in s]
    assert len(alters) == 3
    assert all(s.startswith("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_attribute") for s in alters)


def test_the_pending_index_is_partial_over_the_sessions_without_trusted_groups() -> None:
    create = _normalized(GROUPS.CREATE_PENDING_INDEX)

    assert create == (
        f"CREATE INDEX IF NOT EXISTS {INDEX} ON session_dims (session_id) "
        "WHERE rates_version IS NULL OR model_groups_rev IS DISTINCT FROM 1"
    )
    assert _normalized(GROUPS.CREATE_PENDING_INDEX_CONCURRENTLY) == create.replace(
        "CREATE INDEX", "CREATE INDEX CONCURRENTLY", 1
    )


def test_the_plain_index_step_bounds_the_lock_wait_drops_an_invalid_leftover_and_restores_it() -> None:
    statements = [_normalized(s) for s in GROUPS.index_statements()]

    assert statements == [
        "SET LOCAL lock_timeout = '5s'",
        _normalized(GROUPS.drop_if_invalid(INDEX)),
        _normalized(GROUPS.CREATE_PENDING_INDEX),
        f"SET LOCAL lock_timeout = '{migrations.LOCK_TIMEOUT}'",
    ]
    assert "CONCURRENTLY" not in " ".join(statements)
    drop = statements[1]
    assert f"c.relname = '{INDEX}'" in drop and "NOT i.indisvalid" in drop and f"DROP INDEX {INDEX};" in drop


def _upgrade(rows_above_threshold: bool) -> list[str]:
    """The events of upgrade() in order, with a fake connection that answers the size probe."""
    events: list[str] = []
    bind = MagicMock()
    bind.commit.side_effect = lambda: events.append("COMMIT")
    bind.dialect.default_isolation_level = "READ COMMITTED"
    bind.execution_options.side_effect = lambda **kw: events.append(f"ISOLATION {kw['isolation_level']}") or bind
    bind.execute.side_effect = lambda stmt, *a, **k: (
        events.append(f"BIND {stmt}") or MagicMock(scalar=lambda: rows_above_threshold * (GROUPS.CONCURRENT_ROWS + 1))
    )
    with (
        patch.object(GROUPS.op, "execute", side_effect=events.append),
        patch.object(GROUPS.op, "get_bind", return_value=bind),
    ):
        GROUPS.upgrade()
    return events


def test_a_small_table_gets_a_plain_index_after_the_columns_are_committed() -> None:
    events = _upgrade(rows_above_threshold=False)

    assert events[: len(GROUPS.column_statements())] == GROUPS.column_statements()
    assert events[len(GROUPS.column_statements())] == "COMMIT"
    assert events[-len(GROUPS.index_statements()) :] == GROUPS.index_statements()
    assert not any("ISOLATION" in e or "CONCURRENTLY" in e for e in events)


def test_a_large_table_gets_the_index_concurrently_outside_a_transaction_and_the_isolation_is_restored() -> None:
    events = _upgrade(rows_above_threshold=True)
    text_events = [e for e in events if isinstance(e, str)]

    commit_after_columns = text_events.index("COMMIT")
    assert text_events[:commit_after_columns][: len(GROUPS.column_statements())] == GROUPS.column_statements()
    autocommit = text_events.index("ISOLATION AUTOCOMMIT")
    build = next(i for i, e in enumerate(text_events) if "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in e)
    restored = text_events.index("ISOLATION READ COMMITTED")
    # the last commit precedes the autocommit switch, the build runs inside it, and the restore follows the build
    assert max(i for i, e in enumerate(text_events[:autocommit]) if e == "COMMIT") < autocommit < build < restored
    drop = next(i for i, e in enumerate(text_events) if f"DROP INDEX {INDEX};" in e)
    assert drop < build  # an invalid leftover is dropped first
    assert text_events[restored - 1] == "COMMIT"  # the autobegun transaction ends before the level is restored
    # the drop is a plain DROP INDEX (ACCESS EXCLUSIVE on the table), so it runs under the bounded lock wait ...
    bounded = text_events.index(f"SET lock_timeout = '{GROUPS.INDEX_LOCK_TIMEOUT}'", autocommit)
    assert autocommit < bounded < drop
    # ... and only the build has no bound: CONCURRENTLY waits for older transactions anywhere, and a bound fails it
    unbounded = text_events.index("SET lock_timeout = '0'", autocommit)
    assert drop < unbounded < build
    assert f"SET lock_timeout = '{migrations.LOCK_TIMEOUT}'" in text_events[build:restored]
    assert not any(e == _normalized(GROUPS.CREATE_PENDING_INDEX) for e in text_events)  # not the plain form


def test_the_size_probe_counts_at_most_one_row_above_the_threshold() -> None:
    assert GROUPS.CONCURRENT_ROWS == 150_000
    assert _normalized(GROUPS.COUNT_ROWS) == "SELECT count(*) FROM (SELECT 1 FROM session_dims LIMIT 150001) s"


def test_a_failed_concurrent_build_restores_the_isolation_and_the_lock_wait() -> None:
    events: list[str] = []
    bind = MagicMock()
    bind.dialect.default_isolation_level = "READ COMMITTED"
    bind.commit.side_effect = lambda: events.append("COMMIT")
    bind.execution_options.side_effect = lambda **kw: events.append(f"ISOLATION {kw['isolation_level']}") or bind
    bind.execute.return_value = MagicMock(scalar=lambda: GROUPS.CONCURRENT_ROWS + 1)

    def execute(statement: str) -> None:
        events.append(statement)
        if "CONCURRENTLY" in statement:
            raise RuntimeError("canceling statement due to lock timeout")

    with (
        patch.object(GROUPS.op, "execute", side_effect=execute),
        patch.object(GROUPS.op, "get_bind", return_value=bind),
        pytest.raises(RuntimeError, match="lock timeout"),
    ):
        GROUPS.upgrade()

    # SQLAlchemy autobegins even under AUTOCOMMIT: the connection must end that transaction before the level changes
    assert events[-3:] == [f"SET lock_timeout = '{migrations.LOCK_TIMEOUT}'", "COMMIT", "ISOLATION READ COMMITTED"]


def test_the_downgrade_drops_the_index_the_control_columns_the_stamp_then_the_table() -> None:
    events: list[str] = []
    with patch.object(GROUPS.op, "execute", side_effect=events.append):
        GROUPS.downgrade()

    assert (
        events
        == GROUPS.DOWNGRADE
        == [
            f"DROP INDEX IF EXISTS {INDEX}",
            "ALTER TABLE session_sort_keys_control DROP COLUMN IF EXISTS model_groups_operator_disabled, "
            "DROP COLUMN IF EXISTS model_groups_enabled",
            "ALTER TABLE session_dims DROP COLUMN IF EXISTS model_groups_rev",
            "DROP TABLE IF EXISTS session_model_groups",
        ]
    )
