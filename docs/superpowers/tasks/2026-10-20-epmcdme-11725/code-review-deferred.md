# Deferred from code review — 2026-10-20-epmcdme-11725 (2026-10-20)

- **Negative JSON-pointer index silently wraps instead of erroring** — `src/codemie/workflows/utils/json_utils.py:63` — `int(part)` on a negative string (e.g. `-1`) succeeds and Python list indexing wraps to the last element instead of raising a not-found `KeyError`. Pre-existing: this file and behavior predate this change (unmodified since the `EPMCDME-9519` initial commit) and is out of this ticket's scope (missing/empty iterator list handling in `workflow.py`).
