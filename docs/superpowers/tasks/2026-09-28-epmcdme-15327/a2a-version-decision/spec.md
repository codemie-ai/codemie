# Spec: A2A Version Decision & Scaffolding (EPMCDME-15327, slice 1 of 4)

## Context

CodeMie's A2A layer (`src/codemie/rest_api/a2a/`) is fully in-house: no `a2a-sdk` types are
imported anywhere in it. Its wire shapes (`authentication` field on `AgentCard`, `type`-discriminated
`Part`s, `tasks/send`-style JSON-RPC) match a pre-1.0 A2A draft. The codebase already contains a
working precedent for the newer/target shape: `BedrockAdapter`
(`src/codemie/rest_api/a2a/client/adapter.py`) normalizes remote events that use `kind`
discriminators, the `message/stream` method, and `status-update`/`artifact-update` event kinds back
into CodeMie's legacy types. Separately, `a2ui-agent-sdk 0.5.0` (unrelated feature) transitively pins
`a2a-sdk` to `>=0.3.0,<0.4.0` (resolved `0.3.26`) — a constraint that only matters if CodeMie adopts
`a2a-sdk` directly, which it does not do today.

This slice produces the decision, its rationale, and inert scaffolding only. Slices 2–4
(`a2a-server-protocol`, `a2a-client-protocol`, `a2a-backward-compat`) implement the actual protocol
changes against what this slice decides.

## Decision

1. **Approach: upgrade the in-house implementation. Do not migrate to `a2a-sdk`.**
2. **Target: the latest published A2A protocol specification**, identified for implementation
   purposes by the wire-shape markers already proven in-repo via `BedrockAdapter` — `kind`
   discriminators (not `type`), `message/send`/`message/stream` JSON-RPC methods (not
   `tasks/send`/`tasks/sendSubscribe`), and `status-update`/`artifact-update` event kinds — rather
   than a version literal fixed now. The exact spec version string is confirmed against the
   authoritative spec source by whichever later slice first encodes the wire shape, not guessed
   here.

## Rationale

- **Effort**: CodeMie owns ~1,500 lines of in-house protocol code and ~2,400 lines of tests across
  six files that exercise it end-to-end. An in-house upgrade keeps that regression baseline and
  changes shapes incrementally. An SDK migration transfers none of it — every type, client method,
  server dispatch path, and router integration would be re-plumbed from a standing start.
- **Maintenance**: `BedrockAdapter` already demonstrates the adapter pattern this codebase uses to
  speak a different wire shape without owning it end-to-end — the same pattern extends naturally to
  an in-house upgrade of CodeMie's own client/server types.
- **A2UI conflict**: `a2ui-agent-sdk 0.5.0` pins `a2a-sdk` to `>=0.3.0,<0.4.0` (resolved `0.3.26`),
  for an unrelated feature (agent-authored UI surfaces). Adopting `a2a-sdk` for A2A itself would
  force CodeMie's protocol layer onto that same constrained SDK version range — which may not track
  the "latest published" wire shape this ticket asks for — or require bumping A2UI's pin, an
  unrelated and out-of-scope dependency change. Upgrading in-house sidesteps the conflict entirely:
  no new dependency, no shared pin to negotiate.

## Deliverables

1. **Decision document**: `.ai-run/guides/integration/a2a-integration.md`, following this
   directory's existing `*-integration.md` structure (see `jira-integration.md` /
   `xray-integration.md` for shape). Contents: the decision and rationale above, the target-version
   confirmation method, the wire-shape markers to implement against, and an explicit pointer to the
   three later slices that consume this decision.
2. **Guide registration**: add one row for the new guide to `AGENTS.md`'s Guide Imports table
   (Integrations section) — mechanical, matches the existing pattern for every other integration
   guide.
3. **Version-constants scaffold module**: `src/codemie/rest_api/a2a/version.py`, mirroring the
   naming-convention precedent in `src/codemie/core/a2ui/config.py` (a single module that names a
   version so nothing else has to). Contents:
   - `A2A_PROTOCOL_VERSION: str` — target protocol version string, to be set to the confirmed spec
     version by the slice that first needs it; ships in this slice as an explicitly-labeled
     placeholder plus a docstring pointing at the confirmation method above.
   - A short module docstring naming the wire-shape markers (`kind` discriminator, `message/send`,
     `message/stream`, `status-update`/`artifact-update`) later slices implement against, and
     stating that this module — not a literal re-typed elsewhere — is the one place the target
     version is named.
   - No other constants (no method-name or event-kind constants yet): those belong to the slice
     that actually implements them, to avoid this scaffold guessing at shapes not yet built.

## Acceptance Criteria

- `.ai-run/guides/integration/a2a-integration.md` exists, documents the chosen approach (in-house
  upgrade, not SDK migration), the target-version confirmation method, and the rationale covering
  effort, maintenance, and the A2UI `a2a-sdk` pin conflict.
- `AGENTS.md`'s Guide Imports table includes a row for the new guide.
- `src/codemie/rest_api/a2a/version.py` exists, exports `A2A_PROTOCOL_VERSION`, and its docstring
  names the wire-shape markers and the confirmation method.
- No file under `src/codemie/rest_api/a2a/{types,utils}.py`, `client/*`, `server/*`, or
  `src/codemie/rest_api/routers/a2a.py` changes.
- `pyproject.toml` / `poetry.lock` are unchanged — no new or bumped dependency.
- The existing A2A test suite (router, client, card resolver, remote-agent-connection, task manager,
  utils — ~2,400 lines) passes unmodified, since no runtime behavior changes.

## Non-Goals

- No implementation of the newer wire shapes (`kind` discriminators, `message/send`,
  `message/stream`, `status-update`/`artifact-update`) in `types.py`, `client/*`, or `server/*` —
  deferred to `a2a-server-protocol` / `a2a-client-protocol`.
- No server-side streaming implementation — `on_send_task_subscribe` keeps raising
  `UnsupportedOperationError`.
- No backward-compatibility or version-negotiation mechanism for previously connected remote agents
  — deferred to `a2a-backward-compat`.
- No `a2a-sdk` adoption, and no change to A2UI's existing dependency pin.
- No change to the hardcoded `version="1.0.0"` / `capabilities=AgentCapabilities(streaming=False, ...)`
  literals in `assistant_to_agent_card` — those are CodeMie's own agent-version and capability
  values, not the A2A protocol version this slice scaffolds, and rewiring them is an adoption step
  for a later slice.
- No pinning of the exact numeric target-spec-version literal into `A2A_PROTOCOL_VERSION` — this
  slice ships the confirmation method, not a verified number.

## Open Risks

- The precise "latest published" A2A spec version number is not verified against an external source
  in this slice (no web-fetch tool was available during spec authoring); a later slice must confirm
  it before treating `A2A_PROTOCOL_VERSION`'s placeholder as final.
- AC6 of the parent ticket (behavior of previously connected agents) has no defined mechanism yet;
  this slice only names it as a later slice's responsibility and does not reduce that risk.
