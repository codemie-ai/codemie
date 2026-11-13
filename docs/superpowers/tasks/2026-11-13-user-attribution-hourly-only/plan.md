# Usage Attribution: hourly-ledger figures — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Every Usage Attribution (UA) figure that `litellm_spend_hourly` can produce on its own is read only from that table, through the spend port. Every other figure stays on Elasticsearch (ES) exactly as today. No labels or annotations are added.

**Architecture:** A new UA module `ledger_figures.py` reads platform groups, per-person groups and per-project groups at request time. It uses `spend_service.aggregate_by_spend_group[_per_filter]`, which needs no ES ids, and the filter builders in `analytics/handlers/ledger_group_spend.py`. Each read is clipped to `[F, end)` by the registered `ledger_coverage()`. Handlers overlay the plain figures on the cached ES sections in the response only. The cached sections are never mutated, so ES-only numbers keep their ES operands, for example the repository-group shares.

**Requirements:** inline requirements (EPMCDME-17194, sdlc-light), plus the owner's answers: Q1 (b) strict per-figure; then "all good except no labels". Research: `technical-analysis.md` in this folder.

## Acceptance criteria

- [ ] Each MOVE figure below comes only from `litellm_spend_hourly`, through the spend port, with the caller's `AccessScope`. Each STAY figure is byte-identical to today.
- [ ] No single number, ratio or share included, is built from both engines.
- [ ] Window crossing F: the moved figure is the hourly value for `[F, end)` alone. `[start, F)` contributes nothing, with no ES fill and no flag.
- [ ] Window wholly below F, or coverage not activated: the moved figure has no value (`null`). It is never `0` and never an ES value.
- [ ] No new response keys, labels or flags. The response models, the router and the OpenAPI baseline are unchanged.
- [ ] Nothing outside `service/usage_attribution/` and its tests changes.

## Global constraints

- **Reuse only these, with no new SQL:**
  - `spend_service` (`service/spend/spend_service.py:343-418`)
  - `ledger_group_spend.user_report_filters` / `project_report_filters` (`:83-130`)
  - `render_spend` / `combine_rendered_spend` (`spend/rendering.py`)
  - `ledger_coverage().admitted()` (`spend/coverage.py:215-225`)
- The spend package and the analytics handlers are imported, never edited.
- **Never call `pre_floor_boundary`.** It returns None when `SPEND_LEDGER_LEGACY_BELOW_FLOOR` is off, and the sub-F window would then reach the port's legacy ES leg. Always clip to `admitted()` before calling the port.
- **Never use these reads:** `aggregate_by_dimension`, `aggregate_by_traffic_class`, `aggregate_composed*`. They either resolve ES correlation ids or take no access boundary.
- **Access boundary:** `AnalyticsQueryPipeline(self.user, MetricsElasticRepository()).access_scope` (`analytics/query_pipeline.py:171-196`).
- **Commits:** one per task, subject `EPMCDME-17194: <Capital sentence>`.
- **Never stage these:** `doctor.json`, `.codemie/codemie-cli.config.json`, `config/customer/customer-config.yaml`, `scripts/compare_spend_es_vs_ledger.py`, `litellm_csv/`, or other task directories.
- **Tests:** run narrow pytest on the named file, per `.ai-run/guides/testing/testing-patterns.md`.

## Decisions and assumptions

- **A1 (kept):** a figure the hourly table cannot supply alone stays wholly on ES. A figure is never split between engines.
- **A2, A3:** withdrawn by the owner. A moved total may sit beside ES parts, and these need not add up. No label says so (owner: "no labels").
- **A4:** a person is matched through the port's `UserTerm`: `attr_user_id`, or a parsed principal in `aliases_for_user`. The alias-matched figure is reported as-is. An unresolved uid gets `aliases=None`, which the port handles.
- **A5 (planner):** table membership stays on ES. People and projects are enumerated from the ES sections, so a ledger-only spender with no ES row is not listed. Sorting, filter predicates and shares use the overlaid (ledger) value of a moved column. A `None` figure sorts last and fails every threshold.
- **A6 (planner):** the overlay is cached in `self.service.cache` for `_TOTALS_TTL_S`. The key is `{CACHE_NAMESPACE}:ledger:<view>:<start>:<end>:<stable_hash(access)>`.

### Per-figure classification (reviewer reference; definition notes are not emitted)

| View / key | Verdict | Hourly source | Definition vs ES today |
|---|---|---|---|
| totals `total`; summary `tiles.total` | MOVE | `SpendGroup.TOTAL` | ES = cli_billed + 3 web metrics. Hourly = all spend, including no-customer and other-proxy |
| totals `cli.total`; summary `tiles.cli`; status `tiles.cli` | MOVE | `SpendGroup.CLI` (attr_category/origin) | ES = CLI-proxy docs with `cli_request`, minus no_customer. Hourly includes no-customer |
| totals `web.total`; summary `tiles.web`; pie `platform` | MOVE | `SpendGroup.PLATFORM` (CLI complement) | ES = assistant+workflow+datasource metrics. Hourly = all non-CLI traffic |
| totals `web.datasource`; summary `tiles.web_types.datasource` | MOVE | `SpendGroup.EMBEDDING` (call_type) | ES datasource metric vs embedding call types |
| `web.assistant` | STAY | needs ES correlation ids | — |
| `web.workflow` | STAY | no whole-window, access-bounded class read without ES ids | — |
| `charged_back`, `who_pays.*`, `excluded.no_customer` | STAY | needs `budget_fallback_from` and app-DB funding intervals | — |
| `excluded.other_proxy` | STAY | the `cli_request` flag has no column | — |
| CLI classification tiles (prod/exp/uncl/alloc/ncb*), `categories`, `reasons`, CLI pie slices, `cli_people` | STAY | repository and classifier | — |
| people row `total` / `platform` / `cli` (table and person popup row) | MOVE | per-`UserTerm` TOTAL/PLATFORM/CLI | ES cli = classified billed CLI; ES platform includes fallback records; hourly person = id or alias match |
| people `ncb`, `prod*`, `charged_back`, `shared_no_budget`, `personal_*` | STAY | fallback, repository, classifier | — |
| people footer `total` / `platform` / `cli`, and `share` | MOVE | sum of the matched rows' ledger figures; share = ledger row / ledger total | same as the rows |
| shared-project row `platform` / `cli` (table and popup row), footer, `share` | MOVE | per-project TOTAL/PLATFORM/CLI | ES includes fallback records aimed at the project (`budget_fallback_from`); hourly = `attr_project` |
| shared-project `ncb`, `cli_sh`, `web_sh`, repos, people, clients, models | STAY | fallback, repository, client, `llm_model` | — |
| repository groups, breakdown, people_summary/groups/group, chargeback | STAY (shares keep the ES total) | repository and classifier | — |
| popup models, clients, sessions, assets, workflows, datasources | STAY | `llm_model`, names, sessions; MODEL values cannot be enumerated via the port | — |
| cards (A0) | n/a | used for sizing only, never shown | — |

**Negative-constraint pass:**

| Constraint | How the plan honours it |
|---|---|
| "no double source" | Task 1 refuses any sub-F port call. Tasks 3–4 keep every share single-engine. |
| "unavailable, never an ES fill or a fabricated zero" | Task 1 returns `None`. |
| "no labels" | Task 1 writes the figure only. No task touches the response models. |
| Do not touch the spend port, the per-user handlers, other screens or the kill switch | Global constraints: these are imported, never edited. |
| MR 4839 is not re-implemented | No task touches `project_handler`. |
| Pass the caller's access boundary | Task 1. |

---

### Task 1: Ledger figure reader for Usage Attribution

**Files:**
- Create: `src/codemie/service/usage_attribution/ledger_figures.py`
- Test: `tests/codemie/service/usage_attribution/test_ledger_figures.py`

**Produces:**
```python
def ledger_window(start: datetime, end: datetime) -> TimeRange | None:
    """ledger_coverage().admitted(TimeRange(start, end)): [max(start, F), end), or None
    when wholly below F or coverage is not activated."""
async def platform_groups(user: User, window: TimeRange | None) -> dict[SpendGroup, float | None]
async def person_groups(user: User, window: TimeRange | None, uids: Sequence[str]) -> dict[str, dict[SpendGroup, float | None]]
async def project_groups(user: User, window: TimeRange | None, names: Sequence[str]) -> dict[str, dict[SpendGroup, float | None]]
def sum_figures(values: Iterable[float | None]) -> float | None:
    """combine_rendered_spend(...).figure over the values; None when no value has a figure."""
```

**How each read works:**
- `window is None`: no port call; every group is `None`.
- `platform_groups`: calls `aggregate_by_spend_group(window, access=..., report_filter=None)`.
- `person_groups` / `project_groups`:
  1. Build filters with `user_report_filters(uids, None)` / `project_report_filters(names, None)`.
  2. Chunk them at `HOLDER_BATCH_SIZE` (`spend/ledger.py:139`).
  3. Call `aggregate_by_spend_group_per_filter` once per chunk.
- Each `SpendResult` becomes `render_spend(r).figure`. Partial and unavailable metadata is discarded, never emitted.

Steps:
- [ ] Write the failing tests, patching `ledger_figures.ledger_coverage` and `ledger_figures.spend_service`:
  - A window wholly below F does not await the port, and every value is `None`.
  - Unactivated coverage (`ready=False`) gives the same result.
  - A window crossing F calls the port with exactly `TimeRange(F, end)` and returns the bare figure.
  - The `access` passed equals the pipeline's `access_scope`.
  - The result stays correct with `SPEND_LEDGER_LEGACY_BELOW_FLOOR=False`, and `pre_floor_boundary` is never used.
  - 150 uids produce 2 batched calls.
- [ ] Run the tests and confirm they fail. Implement, run again until they pass, then commit.

Test-first: yes. `person_groups` over a window crossing F must call the port only with `[F, end)` and return the plain float. A window wholly below F must return `None` with no port call.

### Task 2: Totals, summary, CLI summary and status figures from the ledger

**Files:**
- Modify: `service/usage_attribution/handler.py`
- Test: `tests/codemie/service/usage_attribution/test_handler.py` (follow the `TestWebWhoPaysInTotals` setup, L386)

**Changes in `handler.py`:**
- `totals` (L350-398): after the cache hit or miss of the ES block, call `platform_groups(self.user, ledger_window(rng.start, rng.end))`, through the A6 cache. Then set these keys to the ledger value, which may be `None`:

  | Key | Group |
  |---|---|
  | `total` | TOTAL |
  | `cli.total` | CLI |
  | `web.total` | PLATFORM |
  | `web.datasource` | EMBEDDING |

  Round with the existing `r2` when not `None`.
- `cli_summary` (L401-413) and `status` (L699+): overlay a **copy** of `summary["tiles"]`, setting `total`, `cli`, `web` and `web_types.datasource`. Set the `pie` item with `id=="platform"` to PLATFORM.

Steps:
- [ ] Write the failing tests:
  - Above F, `total`, `cli.total`, `web.total` and `web.datasource` equal the patched port values.
  - Above F, `charged_back`, `who_pays`, `excluded`, `web.assistant` and `web.workflow` equal the ES values.
  - Below F, those four keys are `None`, and no key is added anywhere.
  - The cached ES section object is not mutated.
- [ ] Implement, run the tests, commit.

Test-first: yes. With the ledger patched to 10.0 for TOTAL, `totals()["total"]` must read 10.0. Today it returns ES `cli_billed + web_total`.

### Task 3: People table and person popup row from the ledger

**Files:**
- Modify: `handler.py`
- Test: `test_handler.py` (follow the `TestPeopleChargeback` pattern, L98)

**Changes in `handler.py`:**
- `people` (L509-590):
  1. Before filtering, copy **all** section rows and overlay `total`, `platform` and `cli` on them from `person_groups(uids of rows)`.
  2. `with_shares` uses the moved summary TOTAL as its denominator. The share is `None` when either operand is `None`.
  3. The footer's `total`, `platform` and `cli` come from `sum_figures` over the matched rows. The other footer keys stay ES.
- `person` (L803-812): overlay the popup `row` the same way, for that uid.
- `sort_rows` and `_people_predicates` treat `None` as sorting last and not meeting any threshold (A5). Do this with a small key wrapper in `handler.py`, not by changing the shared `sort_rows`.

Steps:
- [ ] Write the failing tests:
  - A row's `total` equals the ledger figure, while its `ncb`, `prod` and `charged_back` equal ES.
  - The share equals ledger row / ledger total.
  - A person with no ledger value sorts last, and the request does not raise.
  - No new keys appear on rows or totals.
  - The `person()` row matches the table row.
- [ ] Implement, run the tests, commit.

Test-first: yes. A people row with ES total 5.0 and ledger figure 7.0 must show `total == 7.0` and `share == 7.0 / ledger_total`. Today it shows 5.0.

### Task 4: Shared projects table and project popup row from the ledger

**Files:**
- Modify: `handler.py`
- Test: `test_handler.py`

**Changes in `handler.py`:**
- `shared_projects` (L415-448):
  1. Copy all rows and overlay `platform` and `cli` on them from `project_groups(names)`.
  2. `with_shares` divides the ledger `platform + cli` by the ledger TOTAL. The share is `None` when either operand is `None`.
  3. The footer's `platform` and `cli` come from `sum_figures`. `ncb`, `cli_sh` and `web_sh` stay ES.
- `shared_project` (L789-801): overlay the popup `row`. `detail` stays ES.
- `repository_groups` (L450-491) and `breakdown` stay unchanged. Confirm they still read the ES `sections[SECTION_SUMMARY]["tiles"]["total"]` and not the overlay.

Steps:
- [ ] Write the failing tests:
  - A project row's `platform` and `cli` equal the ledger values, while its `ncb`, `cli_sh` and `web_sh` equal ES.
  - The share uses ledger operands only.
  - The `repository_groups` share still divides ES spend by the ES total.
  - A project wholly below F has `platform`/`cli` equal to `None`, not 0.
- [ ] Implement, run the tests, commit.

Test-first: yes. A shared-project row with ES platform 3.0 and ledger PLATFORM 4.0 must show `platform == 4.0`, and the `repository_groups` share must still use ES spend over the ES total.

---
Task count: 4 (all Test-first: yes).
