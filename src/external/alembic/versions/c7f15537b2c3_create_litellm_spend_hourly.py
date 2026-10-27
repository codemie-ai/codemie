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

"""create_litellm_spend_hourly

Revision ID: c7f15537b2c3
Revises: c6f15537a1b2
Create Date: 2026-10-05 00:00:00.000000

EPMCDME-15537: the exact hourly projection of the ledger, `litellm_spend_hourly`.

* **What it is.** One row per UTC hour and distinct tuple of the eleven columns every attribution,
  access, report-filter, holder, traffic-class and summary-group predicate reads, carrying the exact
  `numeric` sum of `spend` and the count of ledger rows behind it. A 30-day read then aggregates a
  few hundred thousand hourly rows instead of tens of millions of ledger rows - 16,056,405 ledger rows
  became 138,876 hourly rows (56 MB with indexes) on the dev clone - and `spend_projection` answers
  every routed read from it with figures identical to raw. The ledger stays the authority; this table
  is derived from it and can be rebuilt from it at any time. The owner approved this reversal of the
  ledger's original "no pre-aggregation" rule.
* **Maintained in the ledger write's own transaction.** Four statement-level AFTER triggers on the
  parent - INSERT, UPDATE, DELETE and TRUNCATE; transition tables allow one event per trigger - apply
  grouped, signed deltas (`INSERT … ON CONFLICT … DO UPDATE`, additive) in one fixed total order.
  PostgreSQL hands a parent's statement trigger the rows of every partition, and `INSERT … ON CONFLICT
  DO UPDATE` fires the INSERT and UPDATE triggers, each with its own rows; the UPDATE trigger fires on
  every such statement, often with empty transition tables, and then writes nothing. Exact
  reconciliation was verified on PostgreSQL 14.24 and 17.11 for new rows, changed and unchanged
  re-deliveries, deletes, cross-partition moves and TRUNCATE; run the drift check after the first
  uploader cycle in every environment. Write cost on the dev clone's ten-index ledger: about 1-6 ms
  per real 1,000-row page (33.5 → 34-39 ms), up to 42 ms when every row of a page is a new key.
* **The function is a safe `SECURITY DEFINER`**: owned by the migration role, `search_path =
  pg_catalog, pg_temp`, every persistent object schema-qualified, `EXECUTE` revoked from PUBLIC, and
  it refuses to run for any table but this schema's ledger, so a role holding `EXECUTE` through default
  privileges cannot attach it to a table of its own and forge deltas. The uploader therefore needs no
  privilege on this table, and its contract is unchanged.
* **Privileges.** Migrations run as the backend's own role at startup, which owns the ledger, the
  projection and these functions; a deployment that separates those roles must `GRANT SELECT ON
  litellm_spend_hourly` to the backend's role. The ops functions run as their caller, who needs the
  privileges their statements take: `LOCK` on the ledger, `DELETE`/`INSERT` on the projection, and
  ownership of it for `ANALYZE` to refresh its statistics.
* **The write contract.** One writer at a time, serialized by the uploader's session advisory lock
  (key `5497854265336876612`, `0x4C4C4D5350454E44`); any other writer, and every partition operator,
  takes that lock **before** touching the ledger, as `SELECT pg_advisory_xact_lock(5497854265336876612)`
  - a transaction-level lock, released at commit, never the session-level one, which would hold the
  uploader off silently until the session ends. Anything that changes ledger rows without a parent
  statement - direct partition DML or `COPY`, `ATTACH`/`DETACH PARTITION`, `DROP TABLE <partition>`,
  retention, DEFAULT-partition repair, and any load with the triggers disabled (`DISABLE TRIGGER`,
  `session_replication_role = replica`, a `--disable-triggers` restore) - must run as one transaction:
  READ COMMITTED; the advisory lock; `LOCK TABLE litellm_spend_logs IN SHARE ROW EXCLUSIVE MODE`; the
  ledger change; `SELECT litellm_spend_hourly_rebuild(from_hour, to_hour)` over the aligned affected
  hours; `SELECT litellm_spend_hourly_drift(from_hour, to_hour)` = 0; commit. `DETACH`/`DROP` also
  block ledger readers until that commit. `DETACH … CONCURRENTLY` and other multi-commit procedures
  need `SPEND_LEDGER_PROJECTION_ENABLED` off on every pod until the rebuild has committed. **A parent
  `TRUNCATE`** - like the downgrade - takes ACCESS EXCLUSIVE on the projection after the ledger, while a
  projected reader takes the projection first: run it only with `SPEND_LEDGER_PROJECTION_ENABLED` off on
  every pod and projected sessions drained, or one side aborts with a deadlock (40P01; both tables roll
  back together).
* **Backfilled atomically, here.** The migration requires READ COMMITTED, so every statement takes a
  fresh snapshot, and locks the ledger `SHARE ROW EXCLUSIVE` first: every write committed before the
  lock is in the backfill, and every later write waits and then fires the trigger. Readers are not
  blocked. No readiness flag is needed - the projection is complete the moment it is visible - which
  supersedes the online backfill with a readiness gate the decision record first proposed. In
  production the ledger ships in this release and is empty; on a loaded ledger the uploader waits for
  the whole migration, as it did for `c6f15537a1b2` - the dev clone's backend restart, migration included,
  took about two minutes for its 16M rows, well within the 10-minute startup probe.
* **Ops functions** (`SECURITY INVOKER`, same `search_path`): `litellm_spend_hourly_rebuild(from_hour,
  to_hour)` re-derives whole UTC hours under the ledger lock and returns the rows written; it requires
  READ COMMITTED. `litellm_spend_hourly_drift(from_hour, to_hour)` counts the rows on which the
  projection and the ledger differ over the range, both directions in one statement - 0 means exact.
  Both reject bounds that are not finite whole hours with `from < to`.
* **Key.** A unique expression index - `coalesce('v' || col, 'n')` per nullable column is injective
  (NULL, `''` and `'x'` become `n`, `v` and `vx`) - because PostgreSQL 14 has no `NULLS NOT
  DISTINCT`. The key values are UUIDs, slugs and short enums, at most 366 bytes on the dev clone and far
  below the ~2.7 kB B-tree tuple limit; that is an ingest assumption, and this key adds columns of its
  own (`attr_form`, `attr_user_id`, `attr_principal`, `model_group`, `end_user`) to `c6f15537a1b2`'s
  exposure. `end_user` - the charged customer a personal budget keys on - costs 41 keys more than the
  other ten columns alone produce: it nearly follows from them.
* **Reverting** reads to raw rows is the setting `SPEND_LEDGER_PROJECTION_ENABLED`; the triggers
  keep running until this migration is downgraded, and the setting must be off on every pod before it is.
* **Online only**, like every migration here that reads its connection: it checks the isolation level
  and qualifies its objects with the schema it runs in.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7f15537b2c3"
down_revision: Union[str, None] = "c6f15537a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEDGER = "litellm_spend_logs"
TABLE = "litellm_spend_hourly"

#: Frozen here rather than imported from the model: a migration must not change shape when the model does.
DIMENSIONS: tuple[str, ...] = (
    "attr_source",
    "attr_form",
    "attr_user_id",
    "attr_principal",
    "attr_project",
    "attr_category",
    "codemie_origin",
    "call_type",
    "codemie_workflow_id",
    "model_group",
    "end_user",
)
NULLABLE_DIMENSIONS = DIMENSIONS[2:]

HOUR_OF_START = "date_bin('1 hour', start_time, TIMESTAMPTZ '1970-01-01 00:00:00+00')"
COLUMNS = f"hour, {', '.join(DIMENSIONS)}, spend, row_count"
KEY = "hour, attr_source, attr_form, " + ", ".join(f"(coalesce('v' || {c}, 'n'))" for c in NULLABLE_DIMENSIONS)
GROUP_POSITIONS = ", ".join(str(position) for position in range(1, len(DIMENSIONS) + 2))

TRIGGERS = (
    ("litellm_spend_hourly_after_insert", "INSERT", "REFERENCING NEW TABLE AS new_rows"),
    ("litellm_spend_hourly_after_update", "UPDATE", "REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows"),
    ("litellm_spend_hourly_after_delete", "DELETE", "REFERENCING OLD TABLE AS old_rows"),
    ("litellm_spend_hourly_after_truncate", "TRUNCATE", ""),
)

READ_INDEXES = (
    ("ix_spend_hourly_user_time", "attr_user_id, hour", "attr_user_id"),
    ("ix_spend_hourly_project_time", "attr_project, attr_category, hour", "attr_project"),
    ("ix_spend_hourly_principal_time", "attr_principal, hour", "attr_principal"),
    ("ix_spend_hourly_workflow_time", "codemie_workflow_id, hour", "codemie_workflow_id"),
)

#: Every function runs with nothing resolvable but the catalog: each persistent object is qualified,
#: and pg_temp is last so a temporary table can never shadow one.
SAFE_SEARCH_PATH = "SET search_path = pg_catalog, pg_temp"

#: The bounds check the two ops functions share: finite whole UTC hours, `from < to`.
CHECK_HOURS = """
  IF from_hour IS NULL OR to_hour IS NULL OR NOT isfinite(from_hour) OR NOT isfinite(to_hour)
     OR from_hour >= to_hour
     OR date_bin('1 hour', from_hour, TIMESTAMPTZ '1970-01-01 00:00:00+00') <> from_hour
     OR date_bin('1 hour', to_hour, TIMESTAMPTZ '1970-01-01 00:00:00+00') <> to_hour THEN
    RAISE EXCEPTION 'litellm_spend_hourly: [%, %) must be finite whole UTC hours with from < to', from_hour, to_hour;
  END IF;"""

CHECK_READ_COMMITTED = """
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'litellm_spend_hourly: run under READ COMMITTED, not %', current_setting('transaction_isolation');
  END IF;"""


def grouped(source: str, where: str = "") -> str:
    """The projection of `source`'s rows - shared by the backfill, the rebuild and the drift check, so the
    three can never disagree about a dimension, a NULL or the money's exact `numeric`."""
    return (
        f"SELECT {HOUR_OF_START} AS hour, {', '.join(DIMENSIONS)}, sum(spend) AS spend, count(*) AS row_count "
        f"FROM {source} {where} GROUP BY {GROUP_POSITIONS}"
    )


def upgrade() -> None:
    schema = _require_read_committed_schema()
    ledger, table = f"{schema}.{LEDGER}", f"{schema}.{TABLE}"

    op.execute(f"LOCK TABLE {ledger} IN SHARE ROW EXCLUSIVE MODE")
    nullable = ", ".join(f"{column} text NULL" for column in NULLABLE_DIMENSIONS)
    op.execute(
        f"CREATE TABLE {table} (hour timestamptz NOT NULL, attr_source text NOT NULL, attr_form text NOT NULL, "
        f"{nullable}, spend numeric NOT NULL, row_count bigint NOT NULL)"
    )
    # Filled before its indexes exist: one bulk load and one sort per index, rather than index upkeep per row.
    op.execute(f"INSERT INTO {table} ({COLUMNS}) {grouped(ledger)}")
    op.execute(f"CREATE UNIQUE INDEX ux_spend_hourly_key ON {table} ({KEY})")
    for name, columns, populated in READ_INDEXES:
        op.execute(f"CREATE INDEX {name} ON {table} ({columns}) WHERE {populated} IS NOT NULL")
    op.execute(f"ANALYZE {table}")

    op.execute(_apply_function(schema, table))
    op.execute(f"REVOKE EXECUTE ON FUNCTION {schema}.litellm_spend_hourly_apply() FROM PUBLIC")
    for name, event, referencing in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} AFTER {event} ON {ledger} {referencing} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {schema}.litellm_spend_hourly_apply()"
        )
    op.execute(_rebuild_function(schema, ledger, table))
    op.execute(_drift_function(schema, ledger, table))


def downgrade() -> None:
    schema = _require_read_committed_schema()
    ledger = f"{schema}.{LEDGER}"
    # Both tables up front, ledger first - the writers' order (ledger, then the projection through the
    # trigger) - rather than upgrading a weaker lock mid-way: DROP TRIGGER needs ACCESS EXCLUSIVE. Projected
    # readers take the projection first, so they are kept out only by the precondition that
    # SPEND_LEDGER_PROJECTION_ENABLED is off on every pod before this runs.
    op.execute(f"LOCK TABLE {ledger}, {schema}.{TABLE} IN ACCESS EXCLUSIVE MODE")
    for name, _event, _referencing in TRIGGERS:
        op.execute(f"DROP TRIGGER {name} ON {ledger}")
    op.execute(f"DROP FUNCTION {schema}.litellm_spend_hourly_drift(timestamptz, timestamptz)")
    op.execute(f"DROP FUNCTION {schema}.litellm_spend_hourly_rebuild(timestamptz, timestamptz)")
    op.execute(f"DROP FUNCTION {schema}.litellm_spend_hourly_apply()")
    op.execute(f"DROP TABLE {schema}.{TABLE}")


def _require_read_committed_schema() -> str:
    """The quoted schema the migration runs in, after checking every statement will take a fresh snapshot."""
    connection = op.get_bind()
    isolation = connection.execute(sa.text("SHOW transaction_isolation")).scalar_one()
    if isolation != "read committed":
        raise RuntimeError(f"{revision} must run under READ COMMITTED, not {isolation}")
    current = connection.execute(sa.text("SELECT current_schema()")).scalar_one()
    return connection.dialect.identifier_preparer.quote_schema(current)


def _literal(identifier: str) -> str:
    """A quoted schema name as the SQL string `TG_TABLE_SCHEMA` holds: unquoted, then single-quoted."""
    name = identifier[1:-1].replace('""', '"') if identifier.startswith('"') else identifier
    return "'" + name.replace("'", "''") + "'"


def _apply_function(schema: str, table: str) -> str:
    plus = f"SELECT start_time, {', '.join(DIMENSIONS)}, spend, 1 AS n FROM new_rows"
    minus = f"SELECT start_time, {', '.join(DIMENSIONS)}, -spend AS spend, -1 AS n FROM old_rows"
    cleanup = (
        f"DELETE FROM {table} WHERE row_count = 0 AND spend = 0 AND hour IN (SELECT {HOUR_OF_START} FROM old_rows);"
    )
    return f"""
CREATE FUNCTION {schema}.litellm_spend_hourly_apply() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER {SAFE_SEARCH_PATH} AS $fn$
BEGIN
  IF TG_TABLE_SCHEMA <> {_literal(schema)} OR TG_TABLE_NAME <> '{LEDGER}' THEN
    RAISE EXCEPTION 'litellm_spend_hourly_apply maintains %.{LEDGER} only, not %.%', {_literal(schema)}, TG_TABLE_SCHEMA, TG_TABLE_NAME;
  END IF;
  IF TG_OP = 'INSERT' THEN
    {_upsert(table, plus)}
  ELSIF TG_OP = 'UPDATE' THEN
    {_upsert(table, f"{plus} UNION ALL {minus}")}
    {cleanup}
  ELSIF TG_OP = 'DELETE' THEN
    {_upsert(table, minus)}
    {cleanup}
  ELSE
    TRUNCATE {table};
  END IF;
  RETURN NULL;
END
$fn$"""


def _upsert(table: str, signed_rows: str) -> str:
    """Apply grouped signed deltas in key order; a key whose delta is nothing is not written at all."""
    return (
        f"INSERT INTO {table} AS h ({COLUMNS}) "
        f"SELECT {HOUR_OF_START}, {', '.join(DIMENSIONS)}, sum(spend), sum(n) FROM ({signed_rows}) AS delta "
        f"GROUP BY {GROUP_POSITIONS} HAVING sum(n) <> 0 OR sum(spend) <> 0 ORDER BY {GROUP_POSITIONS} "
        f"ON CONFLICT ({KEY}) DO UPDATE SET spend = h.spend + EXCLUDED.spend, row_count = h.row_count + EXCLUDED.row_count;"
    )


def _rebuild_function(schema: str, ledger: str, table: str) -> str:
    in_range = "WHERE start_time >= from_hour AND start_time < to_hour"
    return f"""
CREATE FUNCTION {schema}.litellm_spend_hourly_rebuild(from_hour timestamptz, to_hour timestamptz) RETURNS bigint
LANGUAGE plpgsql {SAFE_SEARCH_PATH} AS $fn$
DECLARE
  rebuilt bigint;
BEGIN{CHECK_READ_COMMITTED}{CHECK_HOURS}
  LOCK TABLE {ledger} IN SHARE ROW EXCLUSIVE MODE;
  DELETE FROM {table} WHERE hour >= from_hour AND hour < to_hour;
  INSERT INTO {table} ({COLUMNS}) {grouped(ledger, in_range)};
  GET DIAGNOSTICS rebuilt = ROW_COUNT;
  ANALYZE {table};
  RETURN rebuilt;
END
$fn$"""


def _drift_function(schema: str, ledger: str, table: str) -> str:
    raw = grouped(ledger, "WHERE start_time >= from_hour AND start_time < to_hour")
    projected = f"SELECT {COLUMNS} FROM {table} WHERE hour >= from_hour AND hour < to_hour"
    return f"""
CREATE FUNCTION {schema}.litellm_spend_hourly_drift(from_hour timestamptz, to_hour timestamptz) RETURNS bigint
LANGUAGE plpgsql STABLE {SAFE_SEARCH_PATH} AS $fn$
DECLARE
  differences bigint;
BEGIN{CHECK_HOURS}
  SELECT count(*) INTO differences FROM (
    ({raw} EXCEPT ALL {projected})
    UNION ALL
    ({projected} EXCEPT ALL {raw})
  ) AS difference;
  RETURN differences;
END
$fn$"""
