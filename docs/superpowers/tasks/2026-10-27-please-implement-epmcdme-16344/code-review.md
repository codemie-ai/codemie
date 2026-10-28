---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 2c24c8e9abb97a182543268c6972dc3c73256c26
---

# Code review — 2026-10-27-please-implement-epmcdme-16344

**Decision:** approve (final round, confidence high)  
**Reviewed head:** `2c24c8e` · **Rounds:** final

Both expected lenses and standards ran clean; all story criteria pass except AC7, which is pending-stage-7. 4 edge-case candidates dismissed as defensive hardening for typed enterprise result objects. Stage 7 should confirm the installed codemie_enterprise resolver honors client_id_metadata_document_supported=False, since tests mock it. 0 deferred.

## Open — 0

## Coverage

| Acceptance criterion | Status | Notes |
|---|---|---|
| AC1 Setting off, server supports CIMD: CIMD used as today | pass | Result unchanged when flag false |
| AC2 Setting on, both supported: DCR used | pass | Copy with CIMD unsupported passed to resolver; resolver mocked in tests |
| AC3 Setting on, DCR only: DCR as today | pass | Early return when CIMD unsupported |
| AC4 Setting on, CIMD only: stop with 'only CIMD, turned off' message | pass | config_error with cimd_only_and_disabled; wording tested |
| AC5 Setting on, cached DCR registration reused | pass | Resolver still receives dcr_credentials_cache |
| AC6 Toggling does not invalidate existing tokens/connections | pass | Only discovery resolution path touched |
| AC7 Preview/stage with setting on: EPAM MCP Gateway login completes | pending-stage-7 | Manual verification with externally set value |
| AC8 Prod default stays off | pass | MCP_AUTH_CIMD_DISABLED default False; tested |
| Setting MCP_AUTH_CIMD_DISABLED in config.py, default False | pass | config.py:577 |
| Steering in _resolve_discovered_candidate_payload before resolver; non-mutating copy | pass | Handles Mapping/pydantic/dataclass/other |
| Reason code mapped to user-facing text; states only-CIMD and turned off | pass | Constant and action text via failure payload |
| Log the setting's effect once per resolution with sanitized logging | pass | logger.info with _sanitize_log_field |
| Commented default-off setting in deploy-templates/docs if other MCP_AUTH_* listed | na | No other MCP_AUTH_* settings listed; conditional does not apply |
| Tests: matrix, wording, no input mutation, config default | pass | Covered by discovery and config tests |
| Non-goals respected | pass | Only config, constants, discovery bridge and tests touched |

Standards: commit-format pass · code-quality pass · security pass
