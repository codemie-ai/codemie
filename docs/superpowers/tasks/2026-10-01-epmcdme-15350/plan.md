# EPMCDME-15350 A2A docs, wrapper guide and live verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Produce user-facing A2A docs and a wrapper guide for CodeMie, backed by a live verification report.

**Architecture:** Documentation-only. Promote `docs/superpowers/tasks/2026-09-28-epmcdme-15327/DEMO-GUIDE.md` into `docs/a2a-integration.md`; every documented command is executed live first and recorded in `<task_dir>/verification-report.md`. Commit per task using the repository's existing convention.

**Tech Stack:** Markdown, curl, CodeMie backend (Docker/WSL), demo agents from sibling repo `ai-run-demo-agents`.

**Spec:** `docs/superpowers/tasks/2026-10-01-epmcdme-15350/spec.md`

## Global Constraints

- Diff contains only new files under `docs/` and `docs/superpowers/tasks/2026-10-01-epmcdme-15350/`.
- No changes to `src/`, `tests/`, `.ai-run/guides/`, existing task folders, README, CHANGELOG, or already-modified files (`customer-config.yaml`, `customer_config.py`, `doctor.json`, `codemie-cli.config.json`).
- No new unit tests. Defects found are recorded as open risks in the report, not fixed.
- Reference the agents repo; do not copy agent code or details. No video (AC7).

## Task 1: Live happy-path verification (AC1, AC2, AC3)

**Files:** Create `docs/superpowers/tasks/2026-10-01-epmcdme-15350/verification-report.md`

Test-first: no — verification/documentation task; no new unit tests per spec.

- [ ] Follow `DEMO-GUIDE.md` (sections on Docker network layout and UI click path, flagged unrehearsed) to start CodeMie and both agents (`text-insights-agent` :10800, `howto-guide-agent` :10900). Record real requests and responses in the report: both cards fetched, CodeMie calls both over `A2A-Version: 1.0` (one normal reply, one streamed to completion), and an external client doing card, `SendMessage`, `SendStreamingMessage`, `GetTask` against a CodeMie assistant. Note any deviation from DEMO-GUIDE as a correction to carry into the doc.

## Task 2: Live failure and auth runs (AC4)

**Files:** Modify `docs/superpowers/tasks/2026-10-01-epmcdme-15350/verification-report.md`

Test-first: no — live verification; no new unit tests per spec.

- [ ] Run and record request plus actual surfaced error for each: agent stopped (connection refused or timeout, noting `A2A_AGENT_CARD_FETCH_TIMEOUT`/`A2A_AGENT_REQUEST_TIMEOUT`); malformed Agent Card (invalid JSON); Agent Card with no supported interface; missing and invalid bearer on the card endpoint and on JSON-RPC (non-global assistant, plus `is_global` card case); legacy v0.3 agent for the version gate. Record HTTP status versus JSON-RPC error code for each (JSON-RPC errors are HTTP 200; auth errors keep 401/403/404). Any CodeMie defect goes under an "Open risks" heading.

## Task 3: Write `docs/a2a-integration.md` (AC6)

**Files:** Create `docs/a2a-integration.md`

Test-first: no — documentation; no new unit tests per spec.

- [ ] Derive from `DEMO-GUIDE.md` and the report. Sections: setup (`remoteAssistant` flag, `A2A_*` env vars in `src/codemie/configs/config.py:433-436`, auth incl. `user-id` header when `IDP_PROVIDER=local`, stored outbound credentials); CodeMie calling a remote agent; external client calling a CodeMie assistant (card at `/v1/a2a/assistants/{id}/.well-known/agent-card.json`, `SendMessage`, `SendStreamingMessage`, `GetTask`); supported operations (`SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, `GetExtendedAgentCard`); known limitations (v1.0 only, text parts only, no `returnImmediately`, `ListTasks`/`SubscribeToTask`/push return `-32601`, HTTP 200 JSON-RPC errors); failure behavior from Task 2; link to the agents repo.
- [ ] Confirm every command in the doc appears in the report as executed; fix the doc, not the report, where they differ.

## Task 4: Write wrapper guide `docs/a2a-wrapper-guide.md` (AC5)

**Files:** Create `docs/a2a-wrapper-guide.md`

Test-first: no — documentation; no new unit tests per spec.

- [ ] Explain mapping A2A request to existing agent logic (Message text parts to agent input, `contextId`/`taskId`) and result back (Message or Task, artifacts, streaming status/artifact events ending in a final state). Worked example based on `text-insights-agent` (non-streaming) and `howto-guide-agent` (streaming), linking to source in `ai-run-demo-agents`; code only where it pins the mapping. Cover both directions as the spec requires, and link from `docs/a2a-integration.md`.

## Negative-constraint pass

negative-constraints: spec Non-goals honored. No `src/`/`tests/` edits (Tasks 1-4 touch only docs and the task folder); defects are recorded not fixed (Task 2); no agent code copied (Tasks 3-4); no video (not planned); no edits to guides, README, CHANGELOG, or modified files (none listed in any task).
