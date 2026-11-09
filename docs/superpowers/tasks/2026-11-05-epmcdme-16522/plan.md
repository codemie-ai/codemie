# EPMCDME-16522 Unpriced Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A model with no usable rate is listed in `unpriced_models` and never makes `cost_available` true. The CLI Analytics UI shows an unknown cost as unknown, not as a confirmed `$0.00`, and marks an understated cost.

**Architecture:** Backend: one gate in `CatalogPricer._cost` maps a zero-rate `CostConfig` to `None`, so `is_priced`, `price`, `unpriced`, `cost_available`, the list and the detail all agree. UI: the store keeps `metadata.unpriced_models`. A small `SessionCostValue` component renders unknown, understated and priced states for both the sessions list and Session Detail. It reuses `ABSENT_VALUE_MARKER` and the global `react-tooltip` data attributes (`BudgetSpendCell.tsx:115-116`).

**Tech Stack:** Python/pytest (codemie backend); React/TypeScript/Vitest + Testing Library (codemie-ui).

## Acceptance criteria

- A catalog entry with `input == 0`, `output == 0` and no positive cache-read rate makes the model appear in `unpriced_models`. It adds 0 and does not make `cost_available` true, in both the list and the detail.
- Behaviour does not change for any model with a positive input or output rate.
- In the sessions list and Session Detail, a session with `cost_available == false` shows Cost (and, in Detail, Cache-read) as `-` with a tooltip that names the unpriced models. It never shows `$0.00`.
- A session with `cost_available == true` that uses an unpriced model shows its number plus a warning marker. The marker's tooltip says the figure is understated and names the models.
- A fully priced session renders exactly as it does today.
- `fetchSessionDetail` and `fetchSessions` keep `metadata.unpriced_models`.

## Global Constraints

- Backend repo `/Users/Sviatoslav_Likhtarchyk/mdtu_gpt/codemie`, branch `EPMCDME-16522_reproduce-bug`. UI repo `/Users/Sviatoslav_Likhtarchyk/mdtu_gpt/codemie-ui`, new branch `EPMCDME-16522_unpriced-cost-marker` from `origin/main`.
- Commit per task using the repository's existing convention (`EPMCDME-16522: Description`, see each repo's `.ai-run/guides/standards/git-workflow.md`).
- Out of scope (EPMCDME-16644): router aliases, ambiguous tariffs (claude-opus-4-6), undated model names, per-model cost attribution in list metadata.
- UI line numbers below were read at `a577150fa`. Re-confirm them on `origin/main`.

---

### Task 1: Backend — zero-rate entry counts as no usable rate

**Files:**
- Modify: `src/codemie/service/analytics/handlers/coding_agent_pricing.py:112-119` (`_cost`, `is_priced` docstring), class docstring 97-101
- Test: `tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py` (`TestCatalogPricer`, 346-431)
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (next to 97-105)

**Interfaces:** Produces `_has_usable_rate(cost: CostConfig) -> bool` (module-private). `is_priced`/`price` signatures unchanged.

Test-first: yes — a zero-rate `CostConfig` (input 0, output 0, cache_read 0.0, and the same with None) priced with 40,110 cache-read tokens gives `unpriced == [model]` and `is_priced(model) is False`, and a handler list row for it has `cost_available is False`; currently `[]`/True.

- [ ] Step 1: In `TestCatalogPricer`, add a test parametrised over `cache_read_input_token_cost in (0.0, None)`. It patches `get_all_llm_model_info` the way `test_coding_agent_pricing.py:36-37` does, with `CostConfig(input=0.0, output=0.0, cache_read_input_token_cost=X)`, and asserts `price(...) == 0.0`, `unpriced == ["gpt-5.5-2026-04-24"]` and `is_priced(...) is False`. Add two guard tests:
  - `input=0, output=0, cache_read=1e-08` is priced (cost = tokens × rate, not unpriced).
  - `input=0, output=1e-06` is priced (unchanged behaviour).
- [ ] Step 2: In `test_cli_analytics_handler.py`, copy the pattern of `test_priced_session_has_cost_available_true`. Use a catalog with the zero-rate entry and a row with `cache_read_tokens: 40110`. Assert `cost_available is False` and `cost_usd == 0.0` for `get_sessions`. Also call `get_session_detail` and assert its `cost_available` and that its third return value (unpriced) holds the model.
- [ ] Step 3: Run `poetry run pytest tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -v`. The new tests must FAIL.
- [ ] Step 4: In `_cost`, store `None` when the looked-up config fails `_has_usable_rate`:

```python
def _has_usable_rate(cost: CostConfig) -> bool:
    """Zero input and output with no positive cache-read rate is no price, not a free model."""
    return bool(cost.input or cost.output or (cost.cache_read_input_token_cost or 0) > 0)
```

  Update the `is_priced` docstring and the class docstring to say that a zero-rate entry counts as missing. Leave `lookup_cost_config` and `llm_service.get_model_cost` untouched.
- [ ] Step 5: Re-run the Step 3 command. Expect PASS, with no existing test regressing.

### Task 2: UI — branch, types and store keep unpriced metadata

**Files:**
- Modify: `src/types/cliAnalytics.ts:46-66` (`SessionRow`), `:91-101` (`SessionDetail`)
- Modify: `src/store/cliAnalytics.ts:222-231` (`fetchSessions`), `:281-291` (`fetchSessionDetail`)
- Test: `src/store/__tests__/cliAnalytics.test.ts`

**Interfaces:** Produces `SessionRow.cost_available?: boolean` and `SessionRow.unpriced_models?: string[]`. On a list row, `unpriced_models` is the subset of `metadata.unpriced_models` that appears in the row's comma-joined `model_name`. On a detail, it is the full `metadata.unpriced_models`. `SessionDetail` inherits both fields.

Test-first: yes — `fetchSessionDetail` with a response `{data, metadata: {unpriced_models: ["mai-code-1.1-flash"]}}` stores `sessionDetail[id].unpriced_models` equal to that list. `fetchSessions` with rows `"claude-sonnet-4-6, mai-code-1.1-flash"` and `"claude-sonnet-4-6"` gives `["mai-code-1.1-flash"]` and `[]`.

- [ ] Step 1: In the UI repo, run `git fetch origin && git checkout -b EPMCDME-16522_unpriced-cost-marker origin/main`.
- [ ] Step 2: Add the two store tests. Mock the API the way the existing tests in that file do. Run `npx vitest run src/store/__tests__/cliAnalytics.test.ts`. Expect FAIL.
- [ ] Step 3: Add the optional fields to `SessionRow`. Widen the `fetchWithState` generic to `{ data: ...; metadata?: { unpriced_models?: string[] } }`.
  - Detail: store `{ ...data.data, unpriced_models: data.metadata?.unpriced_models ?? [] }`.
  - List: map each row, splitting `model_name` on `,` with trim, and keep the matching unpriced names.
- [ ] Step 4: Re-run the Step 2 command. Expect PASS.

### Task 3: UI — `SessionCostValue` and the sessions list cost column

**Files:**
- Create: `src/pages/analytics/components/cli-analytics/views/SessionCostValue.tsx`
- Modify: `src/pages/analytics/components/cli-analytics/views/SessionsView.tsx:94-96` (`CostCell`)
- Test: `src/pages/analytics/components/cli-analytics/__tests__/SessionsView.test.tsx`

**Interfaces:** Consumes the Task 2 fields. Produces `SessionCostValue: FC<{ value: number | null | undefined; costAvailable?: boolean; unpricedModels?: string[] }>`.

Test-first: yes — SessionsView rows:
- unpriced (`cost_available: false`, `unpriced_models: ["mai-code-1.1-flash"]`) shows `-` with a tooltip naming the model, and no `$0.00`;
- partly priced (`cost_usd: 0.01617`, `cost_available: true`, one unpriced) shows `$0.02` plus the marker;
- priced shows `$0.02` without a marker.

- [ ] Step 1: Add the three tests to `SessionsView.test.tsx`. Assert the tooltip via the `data-tooltip-content` attribute. Run `npx vitest run src/pages/analytics/components/cli-analytics/__tests__/SessionsView.test.tsx`. Expect FAIL.
- [ ] Step 2: Create the component:

```tsx
import { FC } from 'react'

import AlertIcon from '@/assets/icons/alert.svg?react'
import { ABSENT_VALUE_MARKER } from '@/constants'
import { formatCliAnalyticsCost } from '@/utils/currency'

interface SessionCostValueProps {
  value: number | null | undefined
  costAvailable?: boolean
  unpricedModels?: string[]
}

const SessionCostValue: FC<SessionCostValueProps> = ({ value, costAvailable, unpricedModels = [] }) => {
  const names = unpricedModels.join(', ')
  if (costAvailable === false) {
    const tooltip = names ? `Cost unknown: no price for ${names}` : 'Cost unknown: no price for this model'
    return (
      <span data-tooltip-id="react-tooltip" data-tooltip-content={tooltip}>
        {ABSENT_VALUE_MARKER}
      </span>
    )
  }
  const text = value != null ? formatCliAnalyticsCost(value) : ABSENT_VALUE_MARKER
  if (!unpricedModels.length) return <>{text}</>
  return (
    <span className="inline-flex items-center gap-1">
      {text}
      <span
        data-testid="unpriced-cost-marker"
        aria-label="Cost understated"
        data-tooltip-id="react-tooltip"
        data-tooltip-content={`Understated: no price for ${names}`}
      >
        <AlertIcon className="w-3 h-3" />
      </span>
    </span>
  )
}

export default SessionCostValue
```

  `costAvailable === undefined` (an older backend) keeps today's rendering.
- [ ] Step 3: Change `CostCell` to render `<SessionCostValue value={row.cost_usd} costAvailable={row.cost_available} unpricedModels={row.unpriced_models} />` inside the existing `text-right` div.
- [ ] Step 4: Re-run the Step 1 command. Expect PASS, with the existing SessionsView tests unchanged.

### Task 4: UI — Session Detail cost and cache-read cards

**Files:**
- Modify: `src/pages/analytics/components/cli-analytics/views/SessionBody.tsx:109-117` (`costMetrics`) and the `MetricCard` render for those two ids (use the existing `valueOverride?: ReactNode` prop, `widgets/MetricCard.tsx:28`)
- Test: `src/pages/analytics/components/cli-analytics/__tests__/SessionBody.test.tsx` (create; render `SessionBody` with a minimal `SessionDetail` fixture, mocking chart components as `SessionModal.test.tsx` does)

**Interfaces:** Consumes `SessionCostValue` (Task 3) and `SessionDetail.cost_available` / `unpriced_models` (Task 2).

Test-first: yes — a SessionBody with `cost_available: false` and `unpriced_models: ["mai-code-1.1-flash"]` renders no `$0.00` in the Cost or Cache-read cards and shows `-` with the tooltip. A partly priced session shows the number with `unpriced-cost-marker`. A priced session shows the formatted values and no marker.

- [ ] Step 1: Write the three tests. Run `npx vitest run src/pages/analytics/components/cli-analytics/__tests__/SessionBody.test.tsx`. Expect FAIL.
- [ ] Step 2: Pass `valueOverride={<SessionCostValue value=... costAvailable={session.cost_available} unpricedModels={session.unpriced_models} />}` for the `cost_usd` and `cache_read_cost` cards. Cache-read keeps the `—` it shows today when `cache_read_cost_usd == null` and the session is priced. Keep the string value in `m(...)` unchanged for any non-override consumer.
- [ ] Step 3: Re-run the Step 1 command, then `npx vitest run src/pages/analytics/components/cli-analytics`. Expect PASS.

---

## Negative-constraint pass

- "Do not change behaviour for any model with a positive input or output rate": honored by Task 1, whose `_has_usable_rate` is true whenever input or output is non-zero, with guard tests. No task violates it.
- "Apply it at the single gate so list, detail and every money value agree", meaning not per reader: honored by Task 1 (`_cost` only). No handler or router edit.
- "never `$0.00`" for unpriced: honored by Tasks 3 and 4, which assert the absence of `$0.00`.
- Out of scope (aliases, ambiguous tariffs, undated names, per-model attribution in list metadata): no task touches `lookup_cost_config` matching or the backend metadata shape. Task 2 matches names inside the UI only and does not change the API.
- Requirement text "treated exactly like a missing entry": Task 1 stores `None`, so `price()` takes the existing missing-entry branch (127-130).
