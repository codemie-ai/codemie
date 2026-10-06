# Code review — 2026-10-01-epmcdme-14349-project-level-model-availability (2026-10-01)

**request-changes** · confidence: medium · check round 3 of prior_verdict (code-review-final.json) · 22/25 resolved · 3 unresolved · 0 superseded
Coverage: targeted verifier ✓ (25/25 blocking findings graded)

## Still unresolved

- `src/codemie/rest_api/routers/workflow.py:549` — [public API] POST /v1/workflows never enforces the model whitelist — CR-015
- git log (branch) — [other] commit-format — ~14 of ~36 branch commits still lack the EPMCDME-#### prefix — CR-002
- `src/codemie/workflows/nodes/summarize_conversation_node.py:160` — [other] execution fallback model still hardcoded to "gpt-3.5-turbo" instead of llm_service.default_llm_model — CR-022

## Resolved this round

- `src/codemie/core/dependecies.py:266` — [other] CR-004 reversed from unresolved to resolved. get_llm_by_credentials is called unguarded from AIToolsAgent._initialize_llm; any raised ModelAvailabilityException propagates through _ask_assistant's `except ExtendedHTTPException as ehe: raise ehe` and the awaited asyncio.to_thread call into FastAPI's registered @app.exception_handler(ExtendedHTTPException), which returns the exception's own code/message/details/help as a structured error response — satisfying the finding's own "surface a clear user-facing error" recommendation without a new dedicated catch.

## Checked and clean (resolved, carried from prior check round)

CR-001, CR-003, CR-005, CR-006, CR-007, CR-008, CR-009, CR-010, CR-011, CR-012, CR-013, CR-014, CR-016, CR-017, CR-018, CR-019, CR-020, CR-021, CR-023, CR-024, CR-025 — all 21 verified resolved by explicit fix commits against current HEAD; CR-019, CR-013, CR-021 graded identically to the prior check round per instruction.

Full detail: code-review-check.json
