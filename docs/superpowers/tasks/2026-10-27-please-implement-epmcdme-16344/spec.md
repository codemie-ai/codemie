# EPMCDME-16344 — Setting to turn off CIMD and force DCR for MCP OAuth

## Problem
CodeMie always registers with an MCP server's authorization server (AS) via the client metadata document (CIMD) when the AS advertises support. The choice is made inside `codemie_enterprise` (`resolve_client_registration`), which has no switch. On preview/stage hosts reachable only from the EPAM network, the EPAM MCP Gateway cannot download the CIMD, so login fails with a generic "Server Error". DCR needs no download.

## Approach
Add one environment-wide boolean setting, default off. All changes are in this repo; `codemie_enterprise` is not modified.

1. **Setting** in `src/codemie/configs/config.py`, following the `MCP_AUTH_*` convention (proposed name `MCP_AUTH_CIMD_DISABLED: bool = False`). Deploy values live outside the repo (AC7); add it, commented and defaulting to off, to `deploy-templates/` values and the MCP auth doc if the other `MCP_AUTH_*` settings are listed there.
2. **Steering in the bridge** (`src/codemie/enterprise/mcp_auth/_discovery.py`, in `_resolve_discovered_candidate_payload` before `resolve_discovered_oauth2_flow`). When the setting is on:
   - AS supports CIMD and has a `registration_endpoint`: pass the resolver a copy of the discovery result with CIMD support reported as false. The resolver then takes its existing DCR path, including DCR cache hit/miss (AC2, AC5). The original result is not mutated; the copy must work whether the result is an object or a dict (see `_common._get_discovery_result_field`).
   - AS supports CIMD and has no `registration_endpoint`: do not call the resolver. Return a `config_error` payload through `_build_discovered_failure_payload` with a new reason code, e.g. `cimd_only_and_disabled` (AC4).
   - AS does not support CIMD: unchanged (AC3).
   When the setting is off, behavior is byte-for-byte as today (AC1, AC8).
3. **Message (AC4).** The new reason code maps to user-facing text where existing failure reasons are mapped. The text states that the server supports only the client metadata document, which is turned off in this environment. It must not say the server lacks support for it.
4. **Observability.** Log the setting's effect once per resolution, using the existing sanitized logging. The existing `registration_method` log already shows DCR.

## Acceptance criteria
1. Setting off, server supports CIMD: CIMD is used, as today.
2. Setting on, server supports both: DCR is used and login completes.
3. Setting on, server supports only DCR: DCR, as today.
4. Setting on, server supports only CIMD: the connection stops with the "only CIMD, turned off in this environment" message, never "no support".
5. Setting on, DCR registration already cached: it is reused, with no new registration.
6. Toggling the setting does not invalidate existing tokens or connections; no new login is required.
7. Preview/stage with the setting on: login to the EPAM MCP Gateway completes (verified manually; the value is set outside the repo).
8. Prod default stays off; CIMD stays.

## Tests (new, in this repo)
Unit tests for `_resolve_discovered_candidate_payload` covering the matrix: off+CIMD, on+both, on+DCR-only, on+CIMD-only (message wording asserted), on+cache hit (resolver receives the cache, no second registration). The resolver is mocked. Assert the input discovery result is not mutated. Add a config default test.

## Non-goals
- Per-server choice of registration method.
- Fallback to CIMD when no DCR is available (stop with a message instead).
- Declaring the refresh-token grant in DCR (EPMCDME-16327).
- Ingress path for the CIMD document (EPMCDME-16320).
- Stopping CodeMie from serving the CIMD document while the setting is on (router unchanged).
- Any change to the EPAM MCP Gateway or `codemie_enterprise`.
- Migrating or re-registering existing CIMD-registered auth configs.

## Risks / to verify in planning
- Steering relies on the installed resolver honoring `client_id_metadata_document_supported=False` on the passed result; verify against the installed version.
- AC6: confirm that the callback, post-auth and refresh paths never re-run registration, so tokens issued with a CIMD `client_id` keep working.
- Cross-repo coupling: a future enterprise change to the resolver contract could silently defeat the steering; the tests pin the current contract.
