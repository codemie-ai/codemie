# EPMCDME-17180: MR notes

Notes for the MR description. They cover what the architecture review follow-ups (round 2, AR-10 to AR-14;
round 3, AR-15 and AR-16) changed for API consumers and for deployment.

## CLI Insights user details: product decision — Vlad (product)

- Total Cost is the ledger CLI figure for the user (unchanged from the rest of this MR).
- `GET /v1/analytics/cli-insights-user-repositories` (and the details' `repositories_table`) no longer has
  the usage-metric `cost` column or its `spend_*` cells; every other column stays, and repository category
  scoring is unchanged.
- The `est_monthly_20d` estimate is ledger Total Cost / days in the window × 20. It is shown whenever there
  is a figure, partial or truncated included (in that figure's state), and has no figure only when Total
  Cost has none. This replaces the round-1 rule (AR-1) that showed it only for an exact figure.
- The modal's labels and descriptions are `main`'s again: "Est. Monthly (20 days)" with no description,
  Category Breakdown labels unchanged. This supersedes AR-2 and AR-4.
- `GET /v1/analytics/cli-insights-user-category-breakdown` (and the details' `category_breakdown_chart`) no
  longer has the usage-metric `cost` column or its `spend_*` cells: the $ amounts are removed, the
  session-derived shares (`percentage`) and `sessions` are kept. Category classification and ordering are
  unchanged.

## API change: activity endpoints

- `GET /v1/analytics/users-activity` and `GET /v1/analytics/projects-activity`: `cli_cost` is now the
  spend ledger's CLI figure for that user or project, under the dashboard's filter and the caller's
  access boundary. It is the same figure CLI Insights shows for that user or project.
- **`cli_cost` is `null` when there is no figure.** Before, it was `0.0`. There is no figure for the
  unattributed `unknown` project bucket, for a window wholly below the ledger's coverage floor, and when
  the ledger read fails. A user or project with activity but no CLI spend gets an exact `0.0`. A figure
  that is partial, or truncated at the coverage floor, is shown as the bare amount, as on CLI Insights.
- **`total_cost` is now Assistant + Workflow + Datasource + CLI Cost**, the four cost columns the row
  shows, combined by the platform's money rule (`combine_rendered_spend`). When `cli_cost` is `null`, it
  is the sum of the other three. Before, it was the metric documents' total, which held the documents'
  own CLI money and could be smaller than the ledger CLI Cost beside it. This Total combines
  usage-metric web money with ledger CLI money, so it can differ from the Projects Spending figure for
  the same project.
- **The activity endpoints now emit column-prefixed spend flags (AR-15).** Each row carries
  `cli_cost_spend_unavailable`, `cli_cost_spend_partial`, `cli_cost_spend_missing_amount` and the same
  three for `total_cost`, as the routing tables do beside their money columns. A `total_cost` short its
  CLI part (no CLI figure: a failed ledger read, or the `unknown` bucket) is flagged unavailable and
  partial. The amounts are unchanged, and the UI reads no flag from a cell.
- The rows are ordered by that `total_cost`. The footer (`data.totals`) is the shared footer rule
  (`spend_footer`) for both columns: each money key with the same three prefixed flags. The footer
  drops the `cli_cost` key when no row has a CLI figure, and then carries
  `cli_cost_spend_unavailable: true`, which the dashboard footer shows as a dash (it was a blank cell).
- The only caller in the UI is the custom dashboard's Users Activity and Projects Activity widgets.

## Performance: the batched ledger read (AR-12)

Each load of an activity widget now runs one batched ledger read over every candidate row (up to
10,000, `BUCKET_SELECTOR_FETCH_SIZE`, `service/analytics/query_pipeline.py:66`). The CLI Insights tables
already run the same read. Its Postgres half's `WHERE` clause holds only the time window, the caller's
boundary and the dashboard filter. It does not depend on the rows, so one row and ten thousand rows cost
the same Postgres scan.

**That holds for the Postgres half only.** A window that reaches below the coverage floor
(`SPEND_LEDGER_COVERAGE_FLOOR`, `2026-11-01T00:00:00Z`, `configs/config.py:814`), such as "last 30 days"
until 1 December, is answered in two halves, the legacy half first and then the Postgres half, one after
the other (`service/spend/cutover.py:241-245`, `:284`). The legacy half
(`service/spend/legacy_ledger.py:405-417`) is the Elasticsearch ledger. It sends one filter per candidate,
at most 5,000 per search (`FILTER_BATCH_SIZE`, `service/spend/default_ledger.py:100`), and runs the
searches in series (`default_ledger.py:750-761`). Its cost grows with the candidate count: 10,000
candidates are two searches of 5,000 filters each.

**Concurrency.** The read runs after the Elasticsearch query, not beside it. Its per-row filters are
built from the candidate rows that query returns, and the pipeline's row enricher only receives those
rows. No other work in the request is independent of it, so running it concurrently would mean
restructuring the pipeline. That was left out on purpose.

**Plan shape.** `EXPLAIN (ANALYZE, BUFFERS)` was run against the local Postgres (`codemie_13933`,
pgvector/pg17). Scope: admin (unrestricted), no dashboard filter, a 30-day window that does not start
or end on a full hour, hourly projection on (the default, `SPEND_LEDGER_PROJECTION_ENABLED=True`). The
local ledger tables are empty, so the timings mean nothing. Only the plan shape is recorded:

- `GroupAggregate` over a `Sort` on the batch's group key: `(attr_source, attr_form, attr_user_id,
  attr_principal)` for users, `(attr_form, attr_project)` for projects. The aggregate holds
  `HAVING sum(row_count) > 0`.
- Input: an `Append` of three parts:
  - a `Seq Scan` on `litellm_spend_hourly` with the full hours of the window as a filter. On the empty
    table the planner chooses a seq scan. At volume, `ux_spend_hourly_key` (leading column `hour`) can
    serve the range.
  - an `Index Scan` on each raw edge partition, using
    `litellm_spend_logs_YYYY_MM_start_time_spend_attr_category_c_idx` (a covering index on
    `start_time`), for the partial hour at each end. Partitions outside the window are pruned.
- With the projection off, the plan is the same aggregate over index scans on that covering
  `start_time` index, across every raw partition in the window.
- Neither the users read nor the projects read uses a per-user or per-project index. The scan covers
  the whole boundary for the window, as designed. Its cost grows with the number of hourly rows in the
  window, and the per-row assignment then runs in Python on a worker thread.

**Deployment note.** If the batched read is slow at production volume, configure a dedicated ledger
pool: set `SPEND_LEDGER_POOL_SIZE` > 0 together with `SPEND_LEDGER_STATEMENT_TIMEOUT_MS` (see
`src/codemie/configs/config.py:855-861`). With the defaults, the read shares the ten-connection
application pool and has no statement time limit.

- **What the pool and the statement timeout bound: the Postgres half only.** The timeout is a server
  setting on the dedicated engine and nowhere else (`clients/postgres.py:226-237`).
- **What bounds the legacy half: the Elasticsearch per-request timeout, 60 seconds per search.** The
  ledger's searches go through `MetricsElasticRepository.execute_aggregation_query`, whose
  `request_timeout` defaults to `DEFAULT_REQUEST_TIMEOUT` = 60
  (`repository/metrics_elastic_repository.py:58`, `:168`, `:189-193`). It is a module constant, not a
  setting, and it applies per search, so at 10,000 candidates the legacy half can wait up to about two
  minutes before the Postgres half starts. A timed-out search fails its own 5,000 filters
  (`default_ledger.py:762-768`).
- **What a failed or timed-out read looks like.** Every affected row's CLI Cost is a dash, and its
  Total Cost shows the three web parts. The rows are ordered by that Total, and the footers sum what is
  shown. On the wire, the CLI cells and footer are flagged unavailable, and the Total cells and footer
  unavailable and partial (AR-15). The request does not fail.

Before merging, it is worth running the same `EXPLAIN (ANALYZE, BUFFERS)` against a production-sized
copy: admin scope, no filter, last 30 days.

## Production check before merge (AR-16)

Someone with production log access reads the existing timed ledger reads. No new instrumentation is
needed.

- Every ledger read is timed, and one that takes `SPEND_LEDGER_SLOW_READ_SECONDS` (default 5 s,
  `configs/config.py:863`) or more logs the warning
  `spend_service: ledger <name> slow aggregate_by_spend_group_per_filter over <period>: <seconds>s`
  (`service/spend/spend_service.py:401`, `:696-697`). The same durations are on the
  `SPEND_LEDGER_READ_DURATION_METRIC` metric with `read="aggregate_by_spend_group_per_filter"`. The
  duration covers both halves of a read that crosses the floor.
- Read them for the two tables that already run this read over their candidates on "last 30 days":
  the CLI Insights users table (`cli/insights_handler.py:184-196`) and the users-spending CLI table,
  which logs its candidate count as `Users-spending read the cli ledger group for <n> candidate row(s)`
  (`analytics/handlers/user_handler.py:414`). The CLI Insights table logs no count, so take it from the
  same request's Elasticsearch result or from the users-spending line nearby.
- Record the durations beside the candidate counts, and how often the slow-read warning appears.
- **If they are slow, the dedicated pool and statement timeout become a precondition for merging**,
  stated in the MR, with the 60-second legacy-half bound above in mind: the statement timeout does not
  shorten it.

## Rendering rule (AR-13, AR-15)

There is one rule for a money cell's value: `money_cell` in `codemie/service/spend/rendering.py`. Every
table writes its flags beside it, unprefixed (`apply_spend_cells`, CLI Insights) or keyed by the column
(`column_money_cells`, the routing and activity tables). A figure that is exact, partial or truncated at
the coverage floor shows as its amount, and no figure is `null`.

## Reviewer request

This MR touches spend-ledger reads on analytics endpoints. Please post `/sanity` on the MR.
