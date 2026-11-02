# EPMCDME-16533 — [CLI Analytics] Session Detail shows 'Unknown' harness for codemie-claude sessions

Bug. The Session Detail header shows "Unknown" where the harness name should appear (actual `Unknown · claude-sonnet-4-6 · tmp/repo · master · Pure chat`, expected `Claude Code · claude-sonnet-4-6 · tmp/repo · master · Pure chat`).

Cause: `get_session_detail_meta` takes the harness only from `otel_resources.service_name`. A session without an `otel_resources` row (hook events only, e.g. OTLP proxy unhealthy) gets NULL, and `humanize_harness_name(None)` returns "Unknown". Secondary: `HARNESS_LABEL_OVERRIDES` has no `codemie-claude`, so the generic fallback gives "Codemie Claude" (wrong casing).

## Acceptance criteria

1. Given a codemie-claude session completes with a healthy OTLP proxy, when the Session Detail is opened, then the harness field shows 'Claude Code' or 'CodeMie Claude', never 'Unknown'.
2. Given a session where `otel_resources` has `service_name = 'codemie-claude'`, when Session Detail is rendered, then the harness label shows 'CodeMie Claude' with correct casing.
3. Given EPMCDME-16530 is fixed, when new codemie-claude sessions are created and viewed, then the harness field is populated correctly.

## Implementation decisions taken for this MR

- The OTel `service.name` stays the primary source of the harness; `session_dims.platform` (hook `platform`, or derived from OTel span names) is the fallback when no OTel resource exists. The CLI sends `platform='claude-code'` for both claude and codemie-claude, so the fallback shows "Claude Code" (accepted by AC 1).
- Not in scope: fixing EPMCDME-16530 (OTLP proxy health), labels for other `codemie-*` names.
