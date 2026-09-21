# Technical Research

**Task**: aiAdoption feature flag, customer-config.yaml, ai-adoption-framework.md, customer feature configuration guide, release notes
**Generated**: 2026-09-02
**Research path**: filesystem (codegraph MCP unavailable)

---

## 1. Original Context

Backend + docs: retire the aiAdoption feature flag, mark methodology doc retired, update customer config guide. Remove the aiAdoption customer feature flag definition (config/customer/customer-config.yaml lines ~119-124), mark docs/ai-adoption-framework.md as retired via header banner (not deletion) noting dimension-name drift in the TOC, confirm platform boot ignores a customer config that still sets aiAdoption: true without error, locate and update the customer feature configuration guide to drop aiAdoption and state the framework is retired, and draft a release notes entry stating AI/Run Adoption is retired in full listing removed endpoints/SDK methods (may be blocked/placeholder pending ticket 1 SDK-location finding). Out of scope: UI flag gating (none exists), endpoint/handler/query removal (done in ticket 1, already merged), codemie-analytics skill adoption examples (ticket 5). Acceptance criteria: (1) platform starts normally ignoring a lingering aiAdoption:true customer config entry; (2) no removed Analytics/Settings-Administration surfaces reappear for any role; (3) release notes state AI/Run Adoption retired in full and endpoints/SDK methods are gone, not deprecated; (4) customer feature configuration guide no longer offers aiAdoption as configurable and states the framework is retired.

---

## 2. Codebase Findings

### Existing Implementations
- `config/customer/customer-config.yaml` — the `aiAdoption` component block is at **lines 110-115** (ticket text's "119-124" is off by ~9 lines; verify exact offset immediately before editing):
  ```yaml
  - id: "aiAdoption"
    settings:
      enabled: true
      name: "AI Adoption Analytics"
      description: "AI Adoption Framework analytics for measuring and tracking AI maturity across dimensions"
      availableForExternal: false
  ```
  Immediately followed (lines 116-121) by an unrelated `aiChampionsLeaderboard` entry — **must not be touched**.
- `src/codemie/configs/customer_config.py` — `CustomerConfig`/`Component`/`ComponentSetting` models load `customer-config.yaml`. `ComponentSetting` uses `extra="allow"`, so any component id present in YAML but unrecognized elsewhere loads as inert data. `CONFIG_IDS` (lines 24-30) is the map of runtime-computed components; `aiAdoption` is not in it.
- `src/codemie/service/customer_config_declarations.py` — dynamic-config declaration registry; only `chatDisclaimer` is declared (`DECLARATIONS: tuple = (CHAT_DISCLAIMER,)`, line 129). `aiAdoption` has no declaration, so it's never eligible for DB override.
- `src/codemie/service/customer_config_service.py` — `resolve_components()` merges YAML + declared overrides. A leftover `aiAdoption: true` in a customer's YAML just becomes one more `enabled_yaml` component returned from `GET /v1/config`. No exception path exists anywhere for unknown/undeclared component ids, at boot or at request time.
- `src/codemie/rest_api/routers/customer_config.py`, `src/codemie/rest_api/models/customer_config.py` — `GET /v1/config` and admin declarations endpoints; neither references `aiAdoption` by name.
- `docs/ai-adoption-framework.md` (1678 lines) — methodology doc to mark retired via header banner. **Drift found**: the ASCII diagram (lines 203-221) labels "AI Champions" and "AI Capabilities" boxes at **30%** each, while the prose/TOC (lines 21-29) and scoring formulas (lines 989-994) state **20%** each (Daily Active Users 30%, Reusability 30%, AI Champions 20%, AI Capabilities 20%). This is a percentage-drift inside the diagram, not a dimension-naming drift as the ticket phrased it — flag precisely in the retirement banner.
- `docs/superpowers/tasks/2026-08-28-remove-ai-adoption-endpoints/plan.md` and `technical-analysis.md` — ticket 1 (EPMCDME-14465, merged) artifacts. Both explicitly record `customer-config.yaml`'s `aiAdoption` entry and `docs/ai-adoption-framework.md` as out of scope for ticket 1, and note "the Python SDK (separate repo, not checked out here)" as out of scope too — confirming the SDK lives outside this checkout, which is why release-notes SDK-method enumeration must stay a blocked/placeholder item pending that repo's own ticket.

Repo-wide grep for the literal string `aiAdoption` returns exactly 3 hits: the YAML file and the two ticket-1 doc files above. No other source, test, or config file references it.

### Architecture and Layers Affected
- **Configuration layer**: `config/customer/customer-config.yaml` (data), `src/codemie/configs/customer_config.py` (loader/model).
- **Service layer**: `src/codemie/service/customer_config_service.py`, `customer_config_declarations.py` (merge/override logic — read-only touch, no code change expected here since `extra="allow"` already tolerates removal).
- **Docs**: `docs/ai-adoption-framework.md` (retirement banner), `CHANGELOG.md` (release notes entry).
- **No REST/API layer changes** — ticket 1 already removed router/handler/query code; this ticket only removes the flag definition and updates docs.

### Integration Points
- `customer_config_service.resolve_components()` is the single merge point between YAML defaults and DB-declared overrides — this is the correct place to reason about "boot ignores lingering aiAdoption:true," but based on findings **no code change is needed** there; `extra="allow"` + absence from `DECLARATIONS`/`CONFIG_IDS` already guarantees inert pass-through. This should be verified with a new regression test rather than a code change.
- No frontend/UI code in this repo references `aiAdoption` (confirmed out of scope per ticket and prior repo state).

### Patterns and Conventions
- YAML-as-default + optional DB-override-by-declaration pattern: only declared components (`DECLARATIONS` tuple) are dynamically overridable; everything else in YAML passes through inertly via Pydantic `extra="allow"`.
- Release-notes convention (from `CHANGELOG.md`): `## [Unreleased] - yyyy-mm-dd` section at top, one-line bullets formatted `[TICKET-ID](jira-url) Description`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- No file under `.ai-run/guides/` matches `customer-config`, "customer config", or "feature flag" (grep returned no matches) — **there is no in-repo "customer feature configuration guide"**. README.md's "Customer Configuration" section (lines 79-93) is the closest in-repo doc but only covers `tool_defaults` (Jira/Confluence/Git URL pre-fill), not the `components`/feature-flag list where `aiAdoption` lives.
- The ticket's target "customer feature configuration guide" most likely refers to the external public docs site (`https://docs.codemie.ai/`, referenced from README.md line 77 and the `userGuide` component in `customer-config.yaml` lines 20-24) — that content is **not present in this filesystem** and cannot be edited here.

### Architectural Decisions
- `docs/superpowers/runs/20260820-1318-EPMCDME-13983-dynamic-customer-config/design.md` records the customer-config declaration/override architecture (decisions D1-D4) governing why an undeclared component like `aiAdoption` is safely ignorable.

### Derived Conventions
- Retirement/deprecation banners for markdown docs: no existing repo example found; will need to introduce a simple header banner pattern (bold notice + reason + pointer, no deletion of body content).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/configs/test_customer_config.py` — unit tests for `CustomerConfig`/`Component`/`ComponentSetting`, using mocked YAML text (not the real file) — unaffected by removing `aiAdoption` from the real YAML.
- `tests/codemie/service/test_customer_config_service.py`, `test_customer_config_declarations.py`, `test_customer_config_validation.py`, `test_customer_config_audit.py` — service-layer tests, also fixture/mock-based.
- `tests/codemie/rest_api/routers/test_customer_config_router.py`, `tests/codemie/rest_api/security/test_customer_config_write_guard.py` — router/security tests.

### Testing Framework and Patterns
- pytest (Poetry-managed, `poetry run pytest`); mocking pattern: `@patch("codemie.configs.customer_config.Path.read_text")` to inject synthetic YAML instead of reading the real file.

### Coverage Gaps
- No existing test asserts that an undeclared/removed component id (like a lingering `aiAdoption: true`) in a customer's YAML is tolerated without error at boot — new coverage needed for AC #1, following the `@patch(...read_text)` pattern above.
- No test reads the real `config/customer/customer-config.yaml` end-to-end — editing the file directly has no direct regression-test tripwire beyond YAML-parse validity.

---

## 5. Configuration and Environment

### Environment Variables
- None specific to `aiAdoption`. `config.CUSTOMER_CONFIG_DIR` (`customer_config.py` line 79) points at the directory containing `customer-config.yaml`.

### Configuration Files
- `config/customer/customer-config.yaml` — file to edit (remove `aiAdoption` block, lines 110-115; do not touch adjacent `aiChampionsLeaderboard`).
- `README.md` lines 79-93 — no `aiAdoption` mention; no edit needed there.

### Feature Flags and Deployment Concerns
- `aiAdoption` (remove) and `aiChampionsLeaderboard` (keep, adjacent) are the two feature-flag components near the target edit region.
- No Dockerfile/CI/deploy-template references to `aiAdoption` (confirmed independently by this research and by ticket 1's technical-analysis.md).

---

## 6. Risk Indicators

- **Line-number drift**: ticket text says lines 119-124; actual location is lines 110-115. Always re-locate by content match (`id: "aiAdoption"`), not line number, before editing.
- **Adjacent flag risk**: `aiChampionsLeaderboard` sits immediately after `aiAdoption` in the YAML — a careless block deletion could catch it. Edit must be scoped precisely to the `aiAdoption` list item.
- **Missing target file**: no in-repo "customer feature configuration guide" exists. AC #4 cannot be satisfied by editing a file that doesn't exist in this checkout — this is a blocker/scope question, not a code-research gap, and must be surfaced during the clarity check.
- **Cross-repo dependency**: release notes AC #3 needs SDK method names that live in a separate (not checked-out) SDK repo, per ticket 1's own out-of-scope note. This must ship as a placeholder/blocked item unless the user supplies the SDK method names directly.
- **Doc drift inside the file being retired**: the percentage-drift in `docs/ai-adoption-framework.md`'s diagram (30%/30%/20%/20% vs the correct 30/30/20/20 split matching current code's naming) should be called out in the retirement banner so it isn't mistaken for accurate historical record — but is out of scope to "fix" since the doc is being retired, not corrected.
- **No boot-time regression test exists** for AC #1 (undeclared component tolerance) — must be added net-new.

---

## 7. Summary for Complexity Assessment

This is a narrow, well-isolated change touching two layers: a single YAML config block removal (`config/customer/customer-config.yaml`, 5-6 lines) and documentation updates (a retirement banner on a 1678-line doc left otherwise untouched, plus a `CHANGELOG.md` entry). No production Python code changes are required in the config/service layers — the existing `extra="allow"` Pydantic model and undeclared-component pass-through already satisfy AC #1 by design; the work there is adding a regression test to prove it, not writing new logic. Test coverage for the surrounding config-loading machinery is solid and fixture-based, so risk of collateral breakage from the YAML edit is low.

The primary complexity and risk is *non-technical*: AC #4 requires updating a "customer feature configuration guide" that does not exist anywhere in this repository (only an external docs site reference was found), and AC #3's release notes need SDK method names from a separate repository not present in this checkout. Both require a scope decision or user-supplied information before they can be closed out, rather than further code exploration — this should route to the clarity-check step rather than adding technical risk.
