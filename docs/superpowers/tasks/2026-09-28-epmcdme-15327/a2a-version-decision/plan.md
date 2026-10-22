# A2A Version Decision & Scaffolding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the A2A protocol decision (upgrade in-house, not `a2a-sdk`) and its rationale in a guide, register that guide, and scaffold a single version-constants module — no protocol code changes.

**Architecture:** Three independent, additive deliverables: a new integration guide, one new row in an existing table, and one new module mirroring `src/codemie/core/a2ui/config.py`'s "single place a version is named" convention. Nothing under `src/codemie/rest_api/a2a/{types,utils}.py`, `client/*`, `server/*`, `routers/a2a.py`, or `pyproject.toml`/`poetry.lock` is touched.

**Tech Stack:** Markdown docs; Python module with a module-level string constant; pytest for the one new unit test.

**Spec:** `docs/superpowers/tasks/2026-09-28-a2a-version-decision/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- No changes to `src/codemie/rest_api/a2a/{types,utils}.py`, `client/*`, `server/*`, or `src/codemie/rest_api/routers/a2a.py`.
- No `pyproject.toml` / `poetry.lock` changes — no new or bumped dependency, no `a2a-sdk` adoption.
- No change to the hardcoded `version="1.0.0"` / `capabilities=AgentCapabilities(streaming=False, ...)` literals in `assistant_to_agent_card` (`src/codemie/rest_api/a2a/utils.py`).
- `A2A_PROTOCOL_VERSION` ships as an explicitly-labeled placeholder, not a verified spec-version number.
- The existing A2A test suite (router, client, card resolver, remote-agent-connection, task manager, utils — ~2,400 lines, 6 files) is not modified and must keep passing unmodified.

## Review Focus

- A reader of the new guide who has not read the spec must be able to tell, without opening `spec.md`, which of the two options (in-house upgrade vs. SDK migration) was chosen and why — Task 1's content check covers this.
- `AGENTS.md`'s managed table markers (`<!-- ai-run-init:guide-imports start/end -->`) must stay intact after the edit — a malformed marker breaks the table generator on a later run. Task 2's step verifies the markers are still present and the table still parses as one row per line.
- `A2A_PROTOCOL_VERSION`'s placeholder value must be unmistakably a placeholder (not a plausible-looking real version string like `"1.0"`) so nothing downstream mistakes it for a confirmed value — Task 3's test asserts this.
- The new `version.py` module must not import anything from `a2a-sdk` or add any new third-party import — Task 3's test asserts the module has no imports beyond the standard library.
- The guide and the module's docstring must name the same four wire-shape markers (`kind`, `message/send`, `message/stream`, `status-update`/`artifact-update`) so a later slice reading either one gets the same list — Task 1 and Task 3 are cross-checked for this in the self-review below.

---

### Task 1: Write the A2A integration decision guide

**Files:**
- Create: `.ai-run/guides/integration/a2a-integration.md`

**Interfaces:**
- Produces: a guide document later referenced by Task 2's `AGENTS.md` row and read by the three later slices (`a2a-server-protocol`, `a2a-client-protocol`, `a2a-backward-compat`).

Test-first: no — this is a documentation deliverable; there is no executable behavior to pin with a test.

- [ ] **Step 1: Write the guide**

Follow the existing short, table-driven `*-integration.md` shape (see `.ai-run/guides/integration/jira-integration.md` and `xray-integration.md`: a title, one or two short sections, an "Avoid | Prefer" table where relevant, and a closing `Evidence:` line citing concrete file paths). Content to include, drawn from `spec.md`'s Decision/Rationale/Deliverables sections:
  - **Decision**: upgrade the in-house A2A implementation (`src/codemie/rest_api/a2a/`); do not migrate to `a2a-sdk`.
  - **Target version**: the latest published A2A spec, identified by wire-shape markers proven in-repo via `BedrockAdapter` (`src/codemie/rest_api/a2a/client/adapter.py`) — `kind` discriminators (not `type`), `message/send`/`message/stream` JSON-RPC methods (not `tasks/send`/`tasks/sendSubscribe`), and `status-update`/`artifact-update` event kinds — rather than a version literal fixed now.
  - **Confirmation method**: the exact spec version string is confirmed against the authoritative spec source by whichever later slice first encodes the wire shape; not guessed in this guide.
  - **Rationale**: effort (an in-house upgrade keeps ~1,500 lines of protocol code and ~2,400 lines of existing tests as a regression baseline; an SDK migration transfers none of it), maintenance (the `BedrockAdapter` adapter pattern already extends naturally to an in-house upgrade), A2UI conflict (`a2ui-agent-sdk 0.5.0` pins `a2a-sdk` to `>=0.3.0,<0.4.0`, resolved `0.3.26`, for the unrelated A2UI feature; adopting `a2a-sdk` for A2A itself would force CodeMie's protocol layer onto that constrained range or require bumping A2UI's pin — upgrading in-house avoids both).
  - **Pointer to consuming slices**: name `a2a-server-protocol`, `a2a-client-protocol`, and `a2a-backward-compat` as the three later slices that implement against this decision.
  - **Evidence** line citing `src/codemie/rest_api/a2a/client/adapter.py` (BedrockAdapter), `src/codemie/rest_api/a2a/utils.py` (hardcoded `version="1.0.0"`), and `pyproject.toml`/`poetry.lock` (the transitive `a2a-sdk` pin).

- [ ] **Step 2: Commit**

---

### Task 2: Register the new guide in AGENTS.md

**Files:**
- Modify: `AGENTS.md:38-42` (Guide Imports table, Integrations rows, inside the `<!-- ai-run-init:guide-imports start` / `end -->` markers)

**Interfaces:**
- Consumes: the guide path produced by Task 1 (`.ai-run/guides/integration/a2a-integration.md`).

Test-first: no — a one-line table-row addition inside an existing managed table; there is no behavior to test, only a diff to eyeball.

- [ ] **Step 1: Add the row**

Insert a new row after the "X-ray" row (currently `AGENTS.md:39`) and before "Google Docs" (or in the same alphabetical/thematic position the Jira/X-ray/Confluence rows already follow), formatted exactly like its neighbors:

```
| A2A | `.ai-run/guides/integration/a2a-integration.md` | A2A protocol version decision and wire-shape markers |
```

Do not add, remove, or reformat the `<!-- ai-run-init:guide-imports start -->` / `end -->` marker comments themselves — only insert a table row between them.

- [ ] **Step 2: Verify the table still parses**

Read the edited section back and confirm: the markers are unchanged, every row (including the new one) has the same number of `|`-delimited columns as its neighbors, and the new row's guide path matches Task 1's file exactly.

- [ ] **Step 3: Commit**

---

### Task 3: Scaffold `src/codemie/rest_api/a2a/version.py`

**Files:**
- Create: `src/codemie/rest_api/a2a/version.py`
- Test: `tests/codemie/rest_api/a2a/test_version.py`

**Interfaces:**
- Produces: `A2A_PROTOCOL_VERSION: str` — a module-level constant later slices (`a2a-server-protocol`, `a2a-client-protocol`) import and overwrite with the confirmed spec version.

Test-first: yes — failing test asserting `A2A_PROTOCOL_VERSION` exists, is a non-empty string, is unmistakably a placeholder (not a bare real-looking version number), and that the module has no non-stdlib imports.

- [ ] **Step 1: Write the failing test**

```python
import ast
from pathlib import Path

from codemie.rest_api.a2a.version import A2A_PROTOCOL_VERSION


def test_a2a_protocol_version_is_a_labeled_placeholder():
    assert isinstance(A2A_PROTOCOL_VERSION, str)
    assert A2A_PROTOCOL_VERSION.strip()
    assert "placeholder" in A2A_PROTOCOL_VERSION.lower()


def test_version_module_has_no_third_party_imports():
    source = Path("src/codemie/rest_api/a2a/version.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.Import, ast.ImportFrom)), (
            "version.py must not import anything — it is a leaf constants module"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/rest_api/a2a/test_version.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codemie.rest_api.a2a.version'`

- [ ] **Step 3: Write minimal implementation**

```python
"""The single place this backend names the target A2A protocol version.

CodeMie's A2A layer (`src/codemie/rest_api/a2a/`) is fully in-house today; its
current wire shapes (`authentication` field, `type`-discriminated parts,
`tasks/send`-style JSON-RPC) match a pre-1.0 A2A draft. Later slices upgrade it
to the latest published spec, identified by these wire-shape markers (already
proven in-repo via `BedrockAdapter` in `client/adapter.py`):

  - `kind` discriminators on parts and events (not `type`);
  - `message/send` / `message/stream` JSON-RPC methods (not `tasks/send` /
    `tasks/sendSubscribe`);
  - `status-update` / `artifact-update` event kinds.

See `.ai-run/guides/integration/a2a-integration.md` for the full decision and
rationale (in-house upgrade, not `a2a-sdk` migration) and for the confirmation
method: the exact spec version string below is verified against the
authoritative spec source by whichever later slice first encodes the wire
shape above — not guessed here.

This module — not a literal re-typed elsewhere — is the one place the target
A2A protocol version is named.
"""

#: Target A2A protocol version. Placeholder: not yet confirmed against the
#: authoritative spec source. See the confirmation method in this module's
#: docstring and in `.ai-run/guides/integration/a2a-integration.md` before
#: treating this as final.
A2A_PROTOCOL_VERSION: str = "UNCONFIRMED-placeholder-see-a2a-integration-guide"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/rest_api/a2a/test_version.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

## Self-Review

**1. Spec coverage:** Deliverable 1 (decision guide) → Task 1. Deliverable 2 (AGENTS.md row) → Task 2. Deliverable 3 (`version.py` scaffold) → Task 3. All six acceptance criteria in `spec.md` map onto these three tasks; none require touching `types.py`/`utils.py`/`client/*`/`server/*`/`routers/a2a.py` or `pyproject.toml`/`poetry.lock`, and no task does.

**2. Placeholder scan:** No TBD/TODO markers; every step shows concrete content or exact commands.

**3. Type consistency:** `A2A_PROTOCOL_VERSION` is typed and named identically in Task 3's test and implementation; Task 1's guide and Task 3's docstring name the same four wire-shape markers and the same three consuming slice names.

**4. Review Focus:** All five items above are each pinned to a task's own check (Task 1 content review; Task 2's marker/column check; Task 3's placeholder-string and no-import assertions; the cross-check between Task 1 and Task 3 is this self-review itself).

## Negative-Constraint Pass

- "No implementation of the newer wire shapes... in `types.py`, `client/*`, or `server/*`" — honored: no task touches those paths; Task 3 only names the markers in a docstring.
- "No server-side streaming implementation" — honored: no task touches `codemie_task_manager.py` or `on_send_task_subscribe`.
- "No backward-compatibility or version-negotiation mechanism" — honored: no task adds one; Task 1's guide only records that it's deferred.
- "No `a2a-sdk` adoption, and no change to A2UI's existing dependency pin" — honored: no task touches `pyproject.toml`/`poetry.lock`; Task 3's test explicitly asserts `version.py` has zero imports, so it cannot import `a2a-sdk`.
- "No change to the hardcoded `version=\"1.0.0\"` / `capabilities=...` literals in `assistant_to_agent_card`" — honored: no task touches `utils.py`; Task 1 cites that file only as evidence, not as an edit target.
- "No pinning of the exact numeric target-spec-version literal into `A2A_PROTOCOL_VERSION`" — honored: Task 3's value is `"UNCONFIRMED-placeholder-see-a2a-integration-guide"`, and its own test asserts the string contains "placeholder", making a later accidental treat-as-final harder to miss.
- Existing A2A test suite must pass unmodified — honored: Task 3 adds a new test file (`test_version.py`); no existing test file under `tests/codemie/rest_api/a2a/` is modified.
