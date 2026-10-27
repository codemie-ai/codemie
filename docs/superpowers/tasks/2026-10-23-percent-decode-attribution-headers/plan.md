# Decode Percent-Encoded CLI Attribution Headers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decode the percent-encoded `X-CodeMie-Repository`/`X-CodeMie-Branch`/`X-CodeMie-Project` header values the CLI now sends (per `/`-segment, `encodeURIComponent`-style) before they are stored or attributed, via one shared helper applied at every read site, with safe fallback to the raw value for old unencoded CLIs and malformed escapes.

**Architecture:** A single pure decode helper (`decode_attribution_header`) lives in a new small module and is called once, as close to `headers.get(...)` as possible, at each of six sites: the LLM-proxy request-info extraction, the CLI-analytics project gate, two endpoints that gain new header-reading (`metrics.py`, `skill_events.py`), and the four OTLP ingest routes that gain new repository/branch header-reading. No site decodes twice.

**Tech Stack:** Python, `urllib.parse.unquote`, FastAPI `Header(alias=...)`, pytest.

**Requirements:** EPMCDME-16260 (ticket text supplied inline; no `spec.md` — see acceptance criteria below).

Commit per task using the repository's existing convention (ticket-prefixed commit message), one commit per task.

## Acceptance criteria

- [ ] A shared `decode_attribution_header(raw: str) -> str` helper exists in one module and is the only place `unquote` is called for inbound attribution headers.
- [ ] Decoding is per `/`-separated segment: each segment is unquoted independently and rejoined with `/`.
- [ ] A raw value with no percent-escapes round-trips unchanged (no-op for old CLIs).
- [ ] A malformed escape (bare `%`, or a percent-decoded byte sequence that is not valid UTF-8) never raises; the affected segment falls back to its original raw text.
- [ ] A literal `%25` decodes to a literal `%`.
- [ ] `_extract_request_info` (`proxy_router.py`) stores decoded Branch/Repository/Project.
- [ ] `_ProjectIngestGate` (`cli_analytics_project_gate.py`) performs its DB lookup and conflict comparison against the decoded `X-CodeMie-Project` value.
- [ ] `metrics.py`'s `send_metric` and `skill_events.py`'s `record_skill_event` read and decode `X-CodeMie-Repository`/`X-CodeMie-Branch`/`X-CodeMie-Project` when present (new behavior; both endpoints previously ignored these headers).
- [ ] The four OTLP ingest routes (`/logs`, `/metrics`, `/traces`, `/event-hooks` in `cli_analytics.py`) read and decode `X-CodeMie-Repository`/`X-CodeMie-Branch` the same way `X-CodeMie-Project` already does, and the decoded value attributes stored rows that lack their own repository/branch.
- [ ] Unit tests cover the helper's five cases (encoded segment, raw passthrough, malformed-escape fallback, `/`-segment handling, `%25` round trip); each read site has an integration-level test proving stored/attributed output is decoded for encoded input and unchanged for raw input.

**Notes for the reviewer and `mr-creator` (not implementation tasks):**
- This is a **CRITICAL wire-contract change** and must ship, and be verified, **before** the CLI's corresponding encode change is released. The PR must not merge without this sequencing understood by the reviewer.
- Record a **security sign-off** in the PR before merge: decoded values reach OTel metric attributes (`llm_proxy_monitoring_service.py` ~L494-496) and the Postgres project lookup/conflict comparison in the project gate. No raw SQL string formatting of these values was found (repository calls are parameterized, so injection risk is low) — the security reviewer must still confirm this on the actual diff.

## Global Constraints

- Decode per `/`-segment using `encodeURIComponent`'s inverse (`urllib.parse.unquote`), `/` never itself decoded/re-split incorrectly.
- Never raise from the decode helper; any exception falls back to the original raw segment.
- One shared helper; no ad hoc `unquote` calls at any read site.
- Decode exactly once per value — never re-applied downstream of where it's first read.

## Review Focus

- A header value containing an already-decoded `/` inside a segment (e.g. a repository name that legitimately contains `%2F`) — `encodeURIComponent` never encodes a literal `/` as the CLI sends it unescaped, but a value received as `%2F` mid-segment must still just decode to `/` without being mistaken for a path separator inserted by the helper itself.
- An empty header (absent or `""`) — must pass through as `""`/`None` without the helper raising on empty input.
- Two segments where one decodes cleanly and the sibling is malformed — only the malformed segment falls back to raw; the clean segment's decode is not discarded.
- The project gate's case-insensitive DB match must use the *decoded* value — an encoded value here previously would silently discard a whole batch (functional, not just display).
- Non-ASCII multi-byte UTF-8 split across adjacent `%XX` escapes within one segment (e.g. Cyrillic) must decode to the correct single string, not mis-join as separate replacement characters.

---

## Task 1: Shared decode helper

**Files:**
- Create: `src/codemie/core/header_decoding.py`
- Test: `tests/codemie/core/test_header_decoding.py`

**Interfaces:**
- Produces: `decode_attribution_header(raw: str | None) -> str | None` — `None` in, `None` out; otherwise decode each `/`-segment, rejoin with `/`, fall back to the original segment on any decode exception. Every later task imports this symbol from `codemie.core.header_decoding`.

**Test-first: yes — `test_header_decoding.py` asserts all 5 cases below fail against a not-yet-defined `decode_attribution_header`.**

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from codemie.core.header_decoding import decode_attribution_header

@pytest.mark.parametrize("raw,expected", [
    ("clients/%D1%80%D0%B5%D0%BF%D0%BE-%D1%82%D0%B5%D1%81%D1%82", "clients/репо-тест"),
    ("my%20repo", "my repo"),
    ("org/repo", "org/repo"),          # raw passthrough, no-op
    ("100%", "100%"),                 # malformed escape -> fallback to raw segment
    ("%25", "%"),                      # literal percent round trip
    (None, None),
])
def test_decode_attribution_header(raw, expected):
    assert decode_attribution_header(raw) == expected

def test_decode_attribution_header_non_utf8_fallback():
    # a %-escape sequence that decodes to bytes that are not valid UTF-8 falls back to raw
    raw = "clients/%FF%FE"
    assert decode_attribution_header(raw) == raw
```

- [ ] **Step 2: Run `pytest tests/codemie/core/test_header_decoding.py -v` — expect FAIL (module/function not defined)**

- [ ] **Step 3: Implement `decode_attribution_header` in `src/codemie/core/header_decoding.py`**

```python
from urllib.parse import unquote


def decode_attribution_header(raw: str | None) -> str | None:
    """Decode a `/`-separated CLI attribution header value (X-CodeMie-Repository/-Branch/-Project).

    Each segment is decoded independently with `unquote(..., errors="strict")` and rejoined with
    `/`; a segment that isn't valid percent-encoding (or decodes to non-UTF-8 bytes) is left as the
    raw text it arrived as. `None` stays `None`; an unencoded value round-trips unchanged.
    """
    if raw is None:
        return None
    segments = []
    for segment in raw.split("/"):
        try:
            segments.append(unquote(segment, errors="strict"))
        except Exception:
            segments.append(segment)
    return "/".join(segments)
```

- [ ] **Step 4: Run `pytest tests/codemie/core/test_header_decoding.py -v` — expect PASS**

- [ ] **Step 5: Commit**

---

## Task 2: Decode in the LLM-proxy request-info extraction

**Files:**
- Modify: `src/codemie/enterprise/litellm/proxy_router.py:453-477` (`_extract_request_info`)
- Test: `tests/enterprise/litellm/test_proxy_router.py`

**Interfaces:**
- Consumes: `decode_attribution_header` from Task 1.

**Test-first: yes — a test asserting `_extract_request_info` returns a decoded `REPOSITORY`/`BRANCH` for a percent-encoded header, and the raw value unchanged for an unencoded header.**

- [ ] **Step 1: Write failing tests** `test_extract_request_info_decodes_percent_encoded_repository_and_branch` and `test_extract_request_info_raw_header_passthrough` in `test_proxy_router.py`, following the existing `Headers(...)` fixture pattern used by `test_codemie_branch_header_with_unicode_is_stripped`.
- [ ] **Step 2: Run the new tests — expect FAIL.**
- [ ] **Step 3: Wrap the three `headers.get(...)` reads at `proxy_router.py:453-477` with `decode_attribution_header(...)` (import from Task 1); `project` keeps its existing fallback chain (`HEADER_CODEMIE_CLI_PROJECT` or `user.default_project` or `user.username`) with the header branch decoded before the fallback applies.**
- [ ] **Step 4: Run the new tests — expect PASS; run the full `test_proxy_router.py` file to confirm no regression on the existing unicode-stripped test.**
- [ ] **Step 5: Commit**

---

## Task 3: Decode in the CLI-analytics project gate

**Files:**
- Modify: `src/codemie/service/analytics/cli_analytics_project_gate.py:47-49` (`_ProjectIngestGate.__init__`)
- Test: `tests/codemie/rest_api/routers/test_cli_analytics_project_gate.py`

**Interfaces:**
- Consumes: `decode_attribution_header` from Task 1.

**Test-first: yes — a test constructing `_ProjectIngestGate` with a percent-encoded `header_project` whose decoded form matches a stored application name, asserting the gate accepts (no discard reason) instead of returning `project_context_required`.**

- [ ] **Step 1: Write the failing test** `test_gate_accepts_percent_encoded_project_header` (decoded value matches a seeded/mocked application name case-insensitively) and `test_gate_raw_project_header_unchanged` (no regression for old CLIs).
- [ ] **Step 2: Run the new tests — expect FAIL (gate still compares the raw encoded string, so lookup/conflict fails).**
- [ ] **Step 3: In `__init__`, set `self._project = decode_attribution_header(header_project) or ""` instead of `header_project or ""`.**
- [ ] **Step 4: Run the new tests and the existing project-gate test file — expect PASS, no regression.**
- [ ] **Step 5: Commit**

---

## Task 4: Metric attributes constants for repository/branch

**Files:**
- Modify: `src/codemie/service/monitoring/metrics_constants.py:102-124` (`MetricsAttributes`)

**Interfaces:**
- Produces: `MetricsAttributes.REPOSITORY = "repository"`, `MetricsAttributes.BRANCH = "branch"` (new constants; `PROJECT` already exists at line 117). Task 5 consumes these.

**Test-first: no — a two-line constant addition with no independent behavior to test; covered by Task 5's integration test.**

- [ ] **Step 1: Add `REPOSITORY = "repository"` and `BRANCH = "branch"` to `MetricsAttributes`, alongside the existing `PROJECT`.**
- [ ] **Step 2: Commit**

---

## Task 5: New header-reading + decode in `metrics.py` and `skill_events.py`

**Files:**
- Modify: `src/codemie/rest_api/routers/metrics.py:36-89` (`send_metric`)
- Modify: `src/codemie/rest_api/routers/skill_events.py:60-80` (`record_skill_event`) and `src/codemie/service/skill_event_service.py:107-138` (`record`)
- Test: add to the existing test modules for these two routers (create one if none exists for `metrics.py`)

**Interfaces:**
- Consumes: `decode_attribution_header` (Task 1), `MetricsAttributes.REPOSITORY`/`.BRANCH`/`.PROJECT` (Task 4).
- Produces: both endpoints now accept `X-CodeMie-Repository`/`X-CodeMie-Branch`/`X-CodeMie-Project` as optional headers. When a header is present (non-blank after decode), its decoded value is the attributed value; when absent, existing body-field behavior (`skill_events.py`'s `request.repository`/`.branch`/`.project`) is unchanged.

**Test-first: yes — for each endpoint, a test asserting a percent-encoded header produces a decoded attribute/stored value, and a request with no such header is unaffected.**

- [ ] **Step 1: Write failing tests**: `test_send_metric_decodes_repository_branch_project_headers`, `test_send_metric_without_attribution_headers_unaffected` (metrics.py); `test_record_skill_event_header_overrides_decoded`, `test_record_skill_event_without_headers_uses_body_fields` (skill_events.py).
- [ ] **Step 2: Run the new tests — expect FAIL (the headers aren't read today, parametrize at all).**
- [ ] **Step 3: In `metrics.py`, add three `Annotated[str | None, Header(alias=HEADER_CODEMIE_CLI_REPOSITORY/_BRANCH/_PROJECT)]` parameters to `send_metric`; decode each with `decode_attribution_header` and, when non-blank, set `attributes[MetricsAttributes.REPOSITORY/.BRANCH/.PROJECT]`.**
- [ ] **Step 4: In `skill_events.py`, add the same three header parameters to `record_skill_event` and pass the three decoded values through to `skill_event_service.record(...)`; in `record()` (`skill_event_service.py:136-138`), use the decoded header value in place of `request.repository`/`.branch`/`.project` when that header's decoded value is non-blank, otherwise keep the existing body-field value.**
- [ ] **Step 5: Run the new tests — expect PASS.**
- [ ] **Step 6: Commit**

---

## Task 6: New header-reading + decode in the OTLP ingest routes

**Files:**
- Modify: `src/codemie/rest_api/routers/cli_analytics.py:150-152` (`_project_gate`) and the four route handlers at lines 353-393
- Modify: `src/codemie/service/analytics/cli_analytics_project_gate.py` (`_ProjectIngestGate`, `build_ingest_gate`)
- Modify: `src/codemie/repository/cli_analytics/ports.py:85-91` (add `accepted_repository`/`accepted_branch`, mirroring `accepted_project`)
- Modify: `src/codemie/repository/cli_analytics/postgres/ingestor.py:253-315` (`_attributed_to`, `ingest_otlp`, `ingest_hook_events`)
- Test: `tests/codemie/rest_api/routers/test_cli_analytics_ingest.py`

**Interfaces:**
- Consumes: `decode_attribution_header` (Task 1).
- Produces: `_ProjectIngestGate` gains `header_repository`/`header_branch` attributes (decoded, set in `__init__`, analogous to how it already carries `canonical_project`); `build_ingest_gate(user, header_project, header_repository=None, header_branch=None)`. `accepted_repository(gate)`/`accepted_branch(gate)` in `ports.py` read those attributes the same way `accepted_project` reads `canonical_project`.

**Test-first: yes — an OTLP/event-hooks ingest test sending a percent-encoded `X-CodeMie-Repository`/`X-CodeMie-Branch` header with a record whose body carries no repository/branch, asserting the stored row's repository/branch is the decoded header value; a second test with an unencoded header confirms no change in behavior.**

- [ ] **Step 1: Write failing tests** `test_ingest_attributes_repository_and_branch_from_decoded_headers` (percent-encoded) and `test_ingest_repository_branch_header_raw_passthrough` (unencoded) in `test_cli_analytics_ingest.py`, following the existing project-header test fixtures at the line numbers noted in the technical analysis (~L125/413/482/490).
- [ ] **Step 2: Run the new tests — expect FAIL (headers aren't read on these routes today).**
- [ ] **Step 3: In `cli_analytics.py`, read and decode `X-CodeMie-Repository`/`X-CodeMie-Branch` in `_project_gate` and pass them into `build_ingest_gate(user, header_project, header_repository, header_branch)`; in `_ProjectIngestGate.__init__`, store them decoded as `self.header_repository`/`self.header_branch`.**
- [ ] **Step 4: Add `accepted_repository`/`accepted_branch` to `ports.py`, each mirroring `accepted_project`'s `getattr(gate, "...", None)` pattern.**
- [ ] **Step 5: In `ingestor.py`, extend `_attributed_to` (or add a sibling helper applied alongside it) to set a record's repository/branch field to the gate's decoded header value only when that record's own body-supplied repository/branch is blank — across every kind in `_RECORD_KINDS`, not just `batch.hooks`; inspect the row namedtuples in `otlp.py`/`hook_events.py` for the exact repository/branch field names on each kind before wiring this.**
- [ ] **Step 6: Call the extended attribution helper from both `ingest_otlp` and `ingest_hook_events`, passing `accepted_repository(gate)`/`accepted_branch(gate)` alongside the existing `accepted_project(gate)`.**
- [ ] **Step 7: Run the new tests and the full `test_cli_analytics_ingest.py` / project-gate test files — expect PASS, no regression.**
- [ ] **Step 8: Commit**
