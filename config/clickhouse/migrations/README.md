# ClickHouse Migration Guide

The SQL scripts in this directory update materialized views in an existing
ClickHouse deployment. The schema files are applied during initial database
creation and do not modify materialized views that have already been created.

**Note:** The schema files already contain the migration changes, so do not
run these migrations when creating a database from scratch.

## Applying a Migration

Select and apply the migration that corresponds to the target deployment:

- **Standalone or local instance:** `0001_route_cursor_spans.sql`
- **Shared or replicated cluster:** `0001_route_cursor_spans.replicated.sql`

From the repository root, execute the applicable script with
`clickhouse-client`, providing the connection details for the target
ClickHouse instance:

```sh
clickhouse-client --host <host> --user <user> --password <password> \
  --multiquery < config/clickhouse/migrations/0001_route_cursor_spans.sql
```

For a replicated cluster, use
`config/clickhouse/migrations/0001_route_cursor_spans.replicated.sql` as the
input file instead. Apply only the script appropriate to the target deployment.
The migrations are idempotent and may be re-applied safely.
