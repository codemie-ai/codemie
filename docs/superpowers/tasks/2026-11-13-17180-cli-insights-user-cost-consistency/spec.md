# EPMCDME-17180 - CLI Insights user details cost matches the filtered overview and user row

## Problem

The CLI Overview cost card and every CLI Insights user row read the CLI group from the spend ledger
(`service/analytics/handlers/cli/handler.py:99-131`, `cli/insights_handler.py:156-169`). The user-details
modal instead adds up ES `attributes.money_spent` over `codemie_litellm_proxy_usage` documents
(`insights_handler.py:1307-1320`, read at :1382-1432) and projects "Est. Monthly (20 days)" from that sum
(:103-119). The two also match the user differently: the ledger uses the row's `user_id` plus aliases and the
charged project, while ES matches on name, email or id and on `attributes.project`. On top of that, the UI never
passes the row's id to the modal: it reads `item.userId` while rows emit `user_id`
(`codemie-ui/src/pages/analytics/components/CLIInsightsTab.tsx:108` vs `insights_handler.py:924`), so the
backend re-guesses the id from a display name.

## Goal

For the same user, project filter, date range and resolved window, the details' charged cost equals the row's
figure by construction, because both come from the same ledger read. A rolling period is re-resolved to "now" on
every request (`time_parser.py:171-192`), so details opened later can exceed the row by the spend booked in
between. Following the product convention of EPMCDME-16846/16942, a cost the backend has no figure for is
unavailable and shown as a dash, and a partial cost is shown as a bare amount, as the row shows it; the
`spend_*` flags still travel with every money value on the wire. The estimate is projected from that figure.

*Amended by the architecture review follow-up (round 2): the first version said an untrustworthy figure is
never shown as an unqualified amount. The requester replaced that with the product convention above.*

## Acceptance criteria

1. The details `total_cost` (Key Metrics card and detail meta, with `spend_*` flags) is the ledger CLI figure
   for that one user, read narrowly with that user in the ledger query: `_cli_spend_of_user` →
   `dashboard_group_spend(period, access, [user_id], projects, SpendGroup.CLI)`, the read the CLI Overview card
   makes. It is not a batched table read: one person's details have no table to spread a boundary-wide scan
   over. A guard returns no figure, without a read, when no user id resolves, because the one-user read takes an
   empty user list as everyone. It uses the request's `user_id` (or the resolved id when absent) and the dashboard
   `projects` and time window. The batched per-user read the row uses (`group_spend_by_user`) answers the same
   figure for the same filter. For identical inputs and the same resolved window, it equals the row's
   `total_cost` and the CLI Overview `cli_cost` read with `users=[that user]`, subject only to rounding.
2. When no user id resolves, or the ledger read fails, the card stays with no amount and `spend_unavailable`
   set. A ledger `partial` carries through as `spend_partial`. The card never shows `$0.00` for absence.
3. Detail classification and `rule_reasons` take the AC1 figure as their cost input, so an unavailable or partial
   cost is handled as it is in the user list (`get_cli_insights_user_classification`, `insights_handler.py:256`).
   The other classification inputs (repositories, branches, projects) stay scoped to the details
   (`insights_handler.py:1018-1028` vs :1285, :1321-1332), so the details category can still differ from the
   list's.
4. The `est_monthly_20d` card is projected from the AC1 figure with the existing formula (window figure /
   calendar days × 20, `_projected_monthly_spend`). *Amended by product decision (Vlad):* it is shown whenever
   the AC1 figure exists, including a partial or truncated one, and carries that figure's state; only when there
   is no figure is the estimate unavailable too. The card keeps its original label "Est. Monthly (20 days)" and
   has no description.
5. *Amended by product decision (Vlad):* the Repositories table in the modal has no cost column; its other
   columns stay, and repository category scoring is unchanged. The Category Breakdown has no cost either (its
   $ amounts are removed; the session-derived shares and sessions stay — product decision). The modal's descriptions are
   the ones `main` has: the Repositories table has none, and Category Breakdown reads "Repository distribution
   across deterministic CLI categories."
6. Usage metrics in the details (sessions, lines, repositories, and so on) keep their current ES matching.
7. UI: `CLIInsightsTab.tsx` passes the row's `user_id` to the details modal, so the details request carries
   the row's identity.
8. Backend regression tests (`tests/codemie/service/analytics/handlers/test_cli_handler.py`) cover one user with
   a `projects` filter. They prove:
   - the CLI Overview `cli_cost` (`users=[that user]`), the row `total_cost`, and the detail card and meta
     `total_cost` are equal (exact), and the detail matches the row when partial or unavailable, including the
     no-id path;
   - classification and the estimate follow that figure, and the estimate equals figure / 30 × 20 on a 30-day
     window, in the figure's state when that is partial or truncated;
   - the Repositories table carries no cost column, and the labels are `main`'s;
   - for a user whose name and email match only their own records, the details' sessions, lines and
     repositories equal the row's for the same filters.

   A UI test (`CLIInsightsTab.test.tsx`) proves that clicking a row opens the modal with that row's `user_id`.

## Non-goals

- Changing the overview cost path or `report_filter.py`. *Amended:* `ledger_group_spend.py` is no longer
  read-only. It gained `group_spend_by_user` and `group_spend_by_project`, the one composition of a holder's
  figure under the dashboard filter (AR-14). `service/spend/rendering.py` gained `money_cell`, the one rule for a
  money cell without its flags (AR-13).
- *Amended, now in scope:* the Analytics users-activity and projects-activity tables (`user_handler.py`,
  `project_handler.py`) read their CLI Cost from the ledger (AR-5). It is joined through the pipeline's row
  enricher, with Total Cost built from the row's four cost columns (AR-10). The other three cost columns stay on
  the metric documents.
- Tightening the details' ES user or project matching for usage metrics.
- A ledger breakdown of cost per repository or category.
- Changing how `DistributionBarWidget` renders an absent secondary value (`?? 0`, `DistributionBarWidget.tsx:89-100`).
- Router signature or OpenAPI changes; card and meta ids (`total_cost`, `est_monthly_20d`) stay as they are.
- SDK/CLI consumers (none found).

## Constraints

- Money values are `RenderedSpend` and are emitted with `spend_fields`. Never `or 0`.
- The details builder (`_get_cli_insights_user_detail_payload`, `insights_handler.py:479-630`, :1548-1589) is
  split into two sections:
  - **Usage section.** Usage, tools, models, repositories and categories, with no user-level money and no
    classification. `total_cost` and `money_coverage_aggs` are removed from `proxy_usage` (tokens and models
    stay), `spend` / `est_monthly_spend` are dropped from the core metrics (:1404-1429), and the comment at
    :1311-1312 is deleted.
  - **Charged section.** The usage section plus one `RenderedSpend` for the user. *Amended:* read narrowly by
    `_cli_spend_of_user` (AC1), not `_cli_spend_by_user([user_id or ''], ...)[0]`.
    It produces the Total Cost and estimate cards, the meta money, `classification` and `rule_reasons`.

  Only `get_cli_insights_user_detail`, `..._key_metrics` and `..._classification_detail` compose the charged
  section, so each of those requests makes one ledger read, run inside the existing `asyncio.gather`. The other
  detail endpoints make none (`rest_api/routers/analytics/cli_insights.py:214-467`).
- The ledger read passes `self._pipeline.access_scope`, as the rows do.

## Decisions

- Details charged cost is read from the ledger through the row's own per-user read, over re-deriving ledger matching on ES.
- Untrustworthy cost is marked unavailable (the card stays, no amount, `spend_unavailable`), over hiding the card — user *(superseded: AR-1 below)*
- Detail classification and rule reasons follow the ledger figure, over keeping the ES sum — user
- Per-repository and per-category cost stay ES-sourced, marked as metric-attributed, over hiding them or leaving them unqualified — user *(superseded: product decision below)*
- Est. Monthly is projected from the ledger figure and relabelled as a projection, over keeping the label or removing the card — user *(label superseded: product decision below)*
- The UI id hand-off is fixed in this run in codemie-ui, over a backend camelCase alias or a follow-up ticket — user
- Usage metrics keep their current ES matching, over tightening it to `user_id` — user
- The details builder is split into a usage section and a charged section, and the ES money sum is deleted, over a skip-ledger flag in the shared builder or a single details endpoint — user, answers AR-1
- The estimate label states the formula's real basis (20 x daily average over the period), over changing the divisor to working days or a calendar month. *(Superseded: product decision below.)*
- Row and details equality is stated for the same resolved window, over pinning the list request's resolved start and end in the UI.
- Details classification shares only the cost input with the list, over a shared classification-input helper.
- The ES-sourced cost qualifier is "Cost (usage metrics, not charged)", taken from one backend constant for both columns and repeated in the modal description, over "Metric-Attributed Cost" or a new qualifier flag. *(Superseded: AR-2 below.)*
- "Total Cost" keeps its label for the reconciled figure, over renaming it.
- Unavailable cost is a dash, partial cost is a bare amount as the row shows it, and the estimate is unavailable unless the cost is exact, following the EPMCDME-16846/16942 product convention, over marking every untrustworthy figure on screen — user, answers AR-1 *(estimate part superseded: product decision below)*
- Per-repository and per-category cost stay on usage metrics, labelled "Cost from usage metrics (may differ from Total Cost)", with descriptions that explain the scoring, over "Cost (usage metrics, not charged)" — user, answers AR-2 *(superseded: product decision below)*
- The Analytics users-activity and projects-activity CLI Cost moves to the ledger in this ticket, so CLI Insights and Analytics show one CLI cost for a user or project, over a follow-up ticket — user, answers AR-5
- The estimate is labelled "Estimated Monthly Cost" with its basis in the card description, over the long label the card clipped (answers AR-4). *(Superseded: product decision below.)*
- Product decision for the user-details modal: Total Cost stays the ledger figure; the Repositories table drops its usage-metric cost column (other columns and category scoring unchanged); the estimate is ledger Total Cost / days in window × 20, shown whenever there is a figure (partial or truncated included, in that state) and unavailable only when there is none; the modal's labels and descriptions return to `main`'s ("Est. Monthly (20 days)", no estimate description, Category Breakdown cost "Cost" and description "Repository distribution across deterministic CLI categories.", no Repositories note); Category Breakdown $ amounts are removed (cost column and its cells dropped, shares and sessions kept) — Vlad (product). Supersedes the AR-2 and AR-4 decisions and the estimate part of AR-1.
- The activity tables join the ledger CLI Cost through the pipeline's row enricher over every candidate row: one batched read, Total Cost = Assistant + Workflow + Datasource + CLI combined by `combine_rendered_spend` (the amount of the known parts when there is no CLI figure, shown bare and flagged on the wire), rows ordered by that Total and both footers built by `spend_footer` from the figures shown, over filling the visible page afterwards or moving the column back to metric documents (answers AR-10, plan 2; flags per AR-15). This Total combines usage-metric web money with ledger CLI money, so it can differ from the Projects Spending figure for the same project.
