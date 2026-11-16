# spend_widgets

Usage Attribution figures that the hourly spend ledger produces on its own: one class per entity, each with one
public `read`. The handler lays the typed result into its response; nothing here knows about response shapes.

| File | Holds |
|---|---|
| `base.py` | Output types `SpendFigures`, `SpendTable` and `CliUsageFigures` (frozen pydantic dataclasses), `WidgetContext` (caller, shared cache, access scope), `SpendWidget` (the base class, and the coverage-floor window rule) |
| `caching.py` | `@cached_read`, the only place that reads or writes the cache |
| `helpers.py` | What more than one widget shares, for use inside this package only: `round_cents`, `rendered`, `read_per_row`, `no_rows` |
| `widgets.py` | `PlatformSpend`, `PeopleSpend`, `ProjectSpend`, `CliUsageSpend` |

## Rules

- A widget subclasses `SpendWidget[Output]`, sets `view` (its cache-key segment) and `output`, and has exactly one public
  method, `read(start, end[, keys])`, decorated with `@cached_read`. Keys are passed positionally.
- A widget returns a typed output, never a dict. Output types live in `base.py`.
- No widget touches the cache. Caching is only `@cached_read`; change `KEY_VERSION` when the cached shape changes.
- Every read is clipped to `[max(start, F), end)` with `self._window(...)`. Below the floor, or with the ledger not
  activated, a widget returns no figures (`None`, never zero) and does not call the port.
- No figure is topped up from Elasticsearch. A figure the port could not give is `None`, and the output is marked
  `complete=False` so it is not cached.
- The caller's access scope comes from `WidgetContext.scope`, derived once per context. Never build a scope in a widget.
- A widget hands out ready data: figures already in cents. Callers use the typed fields and never import `helpers`;
  logic about a caller's rows (thresholds, sums over a filtered set) lives with the caller.
- Used by one widget only: keep it in that widget's class. Used by two or more widgets: put it in `helpers.py`.
- Responses carry plain numbers only: no labels, flags or `complete` in an API response.

## Adding a widget

1. Add a `SpendWidget` subclass to `widgets.py` with `view`, `output` and a `@cached_read` `read`.
2. Reuse `SpendFigures` or `SpendTable`; add another output dataclass to `base.py` only if neither fits.
3. Export it from `__init__.py`, and add tests under `tests/codemie/service/usage_attribution/spend_widgets/`.

## CLI classification

`CliUsageSpend` reads person/project/repository money through `SpendService.cli_attribution_rows`. The ledger adapter
uses the same projection as the totals and a bounded raw read for repository, branch and client context.
Projection spend without matching raw context remains in the result with missing metadata. The analysis's
`cli_evidence` section supplies classification evidence, and its computation version is part of the widget cache key.
Elasticsearch amounts are evidence only: every displayed CLI category amount and allocation comes from ledger rows.
