# Retire aiAdoption Feature Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `aiAdoption` customer feature flag from `customer-config.yaml`, prove the platform tolerates a lingering `aiAdoption: true` entry in a customer's own config without error, and mark `docs/ai-adoption-framework.md` as retired via a header banner (no content deletion).

**Architecture:** Two independent, self-contained changes: (1) a YAML config edit plus a new regression test in the existing `tests/codemie/configs/test_customer_config.py` suite, following the established `@patch("codemie.configs.customer_config.Path.read_text")` mocking pattern; (2) a documentation-only banner insertion at the top of a 1678-line markdown file, with no changes to the file's existing body.

**Tech Stack:** Python 3.12, Pydantic (`extra="allow"` component model), pytest/unittest, YAML.

## Global Constraints

- Do not touch the `aiChampionsLeaderboard` component block (lines 116-121 of `config/customer/customer-config.yaml`), which sits immediately after `aiAdoption`.
- Do not delete any content from `docs/ai-adoption-framework.md` — retirement is a banner addition only.
- AC #4 (customer feature configuration guide) and AC #3 (release notes / `CHANGELOG.md` entry) are explicitly out of scope for this sub-task per user decision — see "Deferred / Out-of-Repo Items" section below. Do not create files or edit `CHANGELOG.md` for them.

---

## Task 1: Remove the `aiAdoption` component from customer-config.yaml

**Files:**
- Modify: `config/customer/customer-config.yaml:110-115`
- Test: `tests/codemie/configs/test_customer_config.py`

**Interfaces:**
- Consumes: `codemie.configs.customer_config.CustomerConfig` (existing class, loads `config/customer/customer-config.yaml` via `Path.read_text()`), `codemie.configs.customer_config.ComponentSetting` (existing Pydantic model with `extra="allow"`).
- Produces: nothing new consumed by later tasks — Task 2 is independent (docs-only).

**Context:** The real `config/customer/customer-config.yaml` currently contains, at lines 110-115:
```yaml
  - id: "aiAdoption"
    settings:
      enabled: true
      name: "AI Adoption Analytics"
      description: "AI Adoption Framework analytics for measuring and tracking AI maturity across dimensions"
      availableForExternal: false
```
immediately followed at line 116 by the unrelated `aiChampionsLeaderboard` block, which must remain untouched.

`CustomerConfig` (`src/codemie/configs/customer_config.py`) loads this YAML into a list of `Component` objects. `ComponentSetting` uses `extra="allow"`, and no code in the repo reads the `aiAdoption` component id (`DECLARATIONS` in `src/codemie/service/customer_config_declarations.py` doesn't include it, `CONFIG_IDS` in `customer_config.py` doesn't include it). This means an unrecognized/undeclared component in a customer's own YAML — including a lingering `aiAdoption: true` after this repo's default YAML no longer defines it — loads inertly with no exception. This task adds a regression test that proves that behavior, satisfying AC #1 ("platform starts normally with that entry ignored") and AC #2 (no removed surfaces reappear, since nothing consumes the id).

- [ ] **Step 1: Write the failing test**

Add this test to `tests/codemie/configs/test_customer_config.py`, inside `class TestCustomerConfig` (after `test_load_config_successful`, before `test_load_config_invalid_yaml`):

```python
    @patch("codemie.configs.customer_config.Path.read_text")
    def test_load_config_tolerates_retired_ai_adoption_component(self, mock_read_text):
        """A customer YAML that still sets aiAdoption (retired flag) must load without error."""
        yaml_with_retired_flag = {
            'components': [
                {'id': 'component1', 'settings': {'enabled': True}},
                {
                    'id': 'aiAdoption',
                    'settings': {
                        'enabled': True,
                        'name': 'AI Adoption Analytics',
                        'availableForExternal': False,
                    },
                },
            ]
        }
        mock_read_text.return_value = yaml.dump(yaml_with_retired_flag)

        config = CustomerConfig()

        self.assertEqual(len(config.components), 2)
        ai_adoption_components = [c for c in config.components if c.id == 'aiAdoption']
        self.assertEqual(len(ai_adoption_components), 1)
        self.assertTrue(ai_adoption_components[0].settings.enabled)
```

**Test-first: yes — asserts `CustomerConfig()` loads a YAML containing a lingering `aiAdoption` component with no exception and the component is present but inert (nothing in the codebase acts on its id).**

- [ ] **Step 2: Run test to verify it fails or passes for the wrong reason**

Run: `poetry run pytest tests/codemie/configs/test_customer_config.py::TestCustomerConfig::test_load_config_tolerates_retired_ai_adoption_component -v`

Expected: This test is expected to PASS immediately, because `CustomerConfig`/`ComponentSetting` already tolerate unknown components via `extra="allow"` (confirmed by prior technical research — no code path raises on an unrecognized component id). This is a proof-of-behavior regression test, not a red/green TDD cycle for new production code. If it unexpectedly FAILS, stop and investigate `src/codemie/configs/customer_config.py` before proceeding — that would mean the platform does NOT currently tolerate an unknown component, contradicting AC #1, and Task 1 Step 3 below would need to add tolerance logic instead of just deleting the YAML block.

- [ ] **Step 3: Remove the `aiAdoption` block from the real config file**

Edit `config/customer/customer-config.yaml`, deleting exactly lines 110-115 (the `aiAdoption` block). Verify the result by re-reading the file: `aiChampionsLeaderboard` must now be the entry immediately following `angularUpgradeApp` (previously two entries below it), with no blank line artifacts.

Before edit (lines 105-122):
```yaml
      enabled: true
      name: "Angular Upgrade App"
      url: "http://localhost:4173/assets/AngularUpgradeAppRemote.js"
      type: "module"
      description: ""
  - id: "aiAdoption"
    settings:
      enabled: true
      name: "AI Adoption Analytics"
      description: "AI Adoption Framework analytics for measuring and tracking AI maturity across dimensions"
      availableForExternal: false
  - id: "aiChampionsLeaderboard"
    settings:
      enabled: true
      name: "AI Champions Leaderboard"
      description: "Leaderboard analytics showing user AI proficiency scores, tier rankings, and dimension breakdowns"
      availableForExternal: false
  - id: "vendorIntegrationAWS"
```

After edit:
```yaml
      enabled: true
      name: "Angular Upgrade App"
      url: "http://localhost:4173/assets/AngularUpgradeAppRemote.js"
      type: "module"
      description: ""
  - id: "aiChampionsLeaderboard"
    settings:
      enabled: true
      name: "AI Champions Leaderboard"
      description: "Leaderboard analytics showing user AI proficiency scores, tier rankings, and dimension breakdowns"
      availableForExternal: false
  - id: "vendorIntegrationAWS"
```

- [ ] **Step 4: Run the full config test suite to verify nothing broke**

Run: `poetry run pytest tests/codemie/configs/test_customer_config.py -v`

Expected: All tests PASS, including the new `test_load_config_tolerates_retired_ai_adoption_component` (still passes — it uses mocked YAML, unaffected by the real file edit) and all pre-existing tests (they also use mocked YAML via `@patch("codemie.configs.customer_config.Path.read_text")`, so removing the block from the real file has no effect on them).

- [ ] **Step 5: Run the YAML file through a parser to confirm it's still valid**

Run: `poetry run python -c "import yaml; yaml.safe_load(open('config/customer/customer-config.yaml').read())"`

Expected: No exception raised (confirms no indentation/structure damage from the manual edit).

- [ ] **Step 6: Commit**

```bash
git add config/customer/customer-config.yaml tests/codemie/configs/test_customer_config.py
git commit -m "EPMCDME-14467: remove aiAdoption feature flag from customer-config.yaml"
```

---

## Task 2: Mark docs/ai-adoption-framework.md as retired

**Files:**
- Modify: `docs/ai-adoption-framework.md:1-3` (insert banner immediately after the title, before the `---` separator; no other lines touched)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: nothing consumed by other tasks.

**Context:** The file currently starts:
```markdown
# AI Adoption Measurement Framework

---

## Table of Contents
```
Prior research found that the file's internal percentage figures have drifted: the TOC and scoring-formula prose (lines 21-29, ~989-994) state the four dimensions are weighted 30% / 30% / 20% / 20% (Daily Active Users, Reusability, AI Champions, AI Capabilities respectively), but the ASCII box diagram further down (lines ~203-221) shows "AI Champions" and "AI Capabilities" at 30% each instead of 20%. The retirement banner must flag this drift so a future reader doesn't treat the stale diagram percentages as accurate historical record. This task does not fix the drift — the document is being retired, not corrected — it only warns about it.

There is no existing test coverage for markdown documentation content in this repo, so this task has no automated test. Verification is manual (visual read-through of the rendered banner).

- [ ] **Step 1: Insert the retirement banner**

Edit `docs/ai-adoption-framework.md`, replacing:
```markdown
# AI Adoption Measurement Framework

---

## Table of Contents
```
with:
```markdown
# AI Adoption Measurement Framework

> **⚠️ RETIRED:** The AI/Run Adoption Framework has been retired in full. The scoring endpoints, service methods, and SDK methods that implemented this framework have been removed (see EPMCDME-14344 and its sub-tasks). This document is kept for historical reference only and is **not** an active part of the product.
>
> **Known content drift:** the dimension-weight diagram later in this document shows "AI Champions" and "AI Capabilities" at 30% each; the table of contents and scoring formulas elsewhere in this document state the correct historical weighting was 20% each. This predates the retirement and was never corrected — do not treat the diagram's percentages as accurate.

---

## Table of Contents
```

**Test-first: no — this is a documentation-only change with no automated coverage; verified by manual read-through in Step 2.**

- [ ] **Step 2: Manually verify the banner renders correctly**

Read the first 15 lines of `docs/ai-adoption-framework.md` back and confirm: the title is unchanged, the banner block appears immediately after the title using Markdown blockquote (`>`) syntax, the `---` separator and `## Table of Contents` heading that followed the title before the edit are still present and unchanged, and no other content in the 1678-line file was altered.

- [ ] **Step 3: Commit**

```bash
git add docs/ai-adoption-framework.md
git commit -m "EPMCDME-14467: mark AI Adoption Measurement Framework doc as retired"
```

---

## Deferred / Out-of-Repo Items (do not implement — record only)

These two acceptance criteria from the ticket are explicitly out of scope for this sub-task, confirmed with the user during the clarity check:

- **AC #4 (customer feature configuration guide):** No such guide exists anywhere in this repository checkout. The only related reference is the external `https://docs.codemie.ai/` site (linked from `README.md` and the `userGuide` component in `customer-config.yaml`), which is not present in this filesystem and cannot be edited here. This is a follow-up for whoever owns docs publishing on that external site — not an in-repo deliverable.
- **AC #3 (release notes / CHANGELOG.md entry):** Skipped for this sub-task. The entry needs to name specific removed SDK methods, but the SDK lives in a separate repository not checked out here (confirmed via ticket EPMCDME-14465's own out-of-scope note). This is deferred until that SDK repository's own ticket resolves and supplies the method names.

No code or files should be created for either item as part of this plan.
