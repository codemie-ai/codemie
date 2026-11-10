---
schema_version: 1
id: standalone-mcp-servers-visibility
mode: single
repository: codemie
children: []
---
# Standalone MCP Servers Visibility — Story

**Date**: 2026-10-28
**Status**: Approved
**Ticket**: EPMCDME-15689

---

## Context

- MCP has two independent switches. `MCP_CONNECT_ENABLED` (deployment setting) gates MCP tool execution in the backend. `mcpConnect` (customer-config component returned by `GET /v1/config`) controls only what the UI shows. Nothing keeps them in sync today.
- The UI already hides MCP when `mcpConnect` is absent: the MCP Servers section under Tools configuration on the assistant page, the workflow tool selector, the admin MCP page, the MCP settings tab and the MCP onboarding tour steps. An existing UI test covers the assistant page case.
- `MCP_CONNECT_URL` defaults to a local address. The standalone image from EPMCDME-16473 sets it to empty, which is the real "not configured" signal. No deployment-level MCP Connect key setting exists.
- The backend already has precedent for both parts of this change: it drops customer-config components based on deployment settings (leaderboard), and it hides capabilities when a dependency is not configured (Elasticsearch).
- Depends on EPMCDME-16473 (standalone switch and its single gate), which is approved but not yet merged. Only the `codemie` backend changes. `codemie-ui` already reacts to `mcpConnect`, and `codemie-onboarding` is not affected.

---

## Complexity

**Size**: M (18/36) · **Recommended flow**: sdlc-standard

The change spans the shared customer-config resolution, the startup sequence and the MCP runtime enforcement points. Graceful degradation with an empty `MCP_CONNECT_URL` is untested today, and the story depends on the unmerged EPMCDME-16473 gate.

---

## Story

**As a** platform operator running CodeMie in standalone mode, **I want** MCP features to appear only when MCP Connect is enabled and configured **so that** users never see or rely on MCP functionality that cannot work in my deployment.

---

## Background

Standalone deployments run with a minimal set of dependencies, and MCP Connect is often absent. Today the UI still shows MCP Servers and the related MCP entry points, and saved MCP servers fail at runtime with raw connection errors. Standalone mode needs to recognise when MCP is not usable, hide it in the UI, and degrade gracefully at runtime. Regular deployments must stay exactly as they are.

---

## Acceptance Criteria

- [ ] Given a non-standalone deployment, when `GET /v1/config` is called and assistants or workflows use MCP, then the behaviour is identical to today, whatever the values of `MCP_CONNECT_ENABLED` and `MCP_CONNECT_URL`.
- [ ] Given standalone mode with `MCP_CONNECT_ENABLED` true and `MCP_CONNECT_URL` set, when `GET /v1/config` is called, then `mcpConnect` is returned as configured in customer config, and MCP Servers can be displayed and used.
- [ ] Given standalone mode with `MCP_CONNECT_ENABLED` false, when `GET /v1/config` is called, then `mcpConnect` is not returned as available.
- [ ] Given standalone mode with `MCP_CONNECT_URL` empty or missing, when `GET /v1/config` is called, then `mcpConnect` is not returned as available.
- [ ] Given `mcpConnect` is not available, when a user opens the assistant configuration page and expands Tools configuration, then the MCP Servers section is not shown and the rest of the page works normally.
- [ ] Given `mcpConnect` is not available, when a user opens the workflow tool selector, the admin MCP page, the settings tabs or the onboarding tour, then the MCP entry points are hidden, as they already are when `mcpConnect` is absent.
- [ ] Given standalone mode with MCP unavailable and an assistant that has saved MCP servers, when the assistant is used, then it answers without its MCP tools, does not raise a connection error, and the backend logs a warning.
- [ ] Given standalone mode with MCP unavailable and a workflow with an MCP tool node, when that node runs, then it fails with a clear "MCP not available" message instead of a raw connection error.
- [ ] Given standalone mode with `MCP_CONNECT_URL` empty or missing, when the application starts, then it logs a warning that MCP Connect is not configured and lists the degraded features: MCP Servers on assistants, workflow MCP tools, the admin MCP page, the MCP settings tab and the MCP onboarding steps.
- [ ] Given a non-standalone deployment with `MCP_CONNECT_URL` empty, when the application starts, then no new MCP warning is logged.
- [ ] Given this change, when it is reviewed, then no new feature flag has been added. Only the existing `MCP_CONNECT_ENABLED` and `mcpConnect` are used.
- [ ] Given automated tests, when they run, then they cover standalone on and off, `MCP_CONNECT_ENABLED` on and off, and `MCP_CONNECT_URL` set and empty, for both the config response and the runtime MCP behaviour.

---

## Out of Scope

- A deployment-level MCP Connect key setting.
- A user-facing notice in chat that MCP tools were skipped.
- Any change to non-standalone behaviour, including startup logging.
- Dependency checks and startup warnings for other services (Elasticsearch, Mermaid, Redis, web search).
- UI code changes beyond tests. The UI already hides MCP when `mcpConnect` is absent.
- Removing or migrating MCP servers already saved on assistants.

---

## Decisions

- D1: What counts as "MCP Connect configured"? → `MCP_CONNECT_URL` is set and not empty. "Key" is dropped because no such setting exists — user
- D2: Which flag is "the MCP feature flag"? → Reuse `MCP_CONNECT_ENABLED`. In standalone mode, `mcpConnect` is available only when `MCP_CONNECT_ENABLED` is true and `MCP_CONNECT_URL` is set. No new flag — user
- D3: Where is MCP hidden? → On every surface that reads `mcpConnect`, not only the assistant page — user
- D4: What happens to saved MCP servers at runtime when MCP is unavailable? → Assistants skip MCP tools, workflow MCP nodes fail with a clear message, and the backend logs a warning — user
- D5: Startup warning for an empty `MCP_CONNECT_URL`? → Added as a requirement. Standalone only, and it lists the degraded features — user
- D6: Startup warning when `MCP_CONNECT_ENABLED` is explicitly false in standalone? → No warning, because the operator turned MCP off deliberately — proposed by PO: the user's requirement names only the empty URL

---

## Constraints

- Lands after EPMCDME-16473 (`STANDALONE` switch and single gate). All standalone-only logic in this story goes through that gate. Source: `docs/stories/2026-10-28-standalone-flag-dependency-checks/story.md`.
- The standalone setting itself stays hidden from customer config and the UI (EPMCDME-16473). The UI learns about availability only through `mcpConnect`.
- End-to-end check: in a standalone deployment with an empty `MCP_CONNECT_URL`, the assistant page shows no MCP Servers section. With the URL set and `MCP_CONNECT_ENABLED` true, the section is shown and works.
- Workspace landing-order and owner-approval rules: not found in the workspace `CLAUDE.md`.

---

## Sources

- `codemie` · repository (change-owning)
- `codemie-ui` · repository (read-only, existing `mcpConnect` consumer)
- `codemie-onboarding` · repository (read-only, not affected)

---

## Open Questions

- None
