# Code review — 2026-10-06-epmcdme-15555-hide-tool-outputs (2026-10-06)

**request-changes** · confidence: low · 2/2 prior findings still unresolved
Coverage: targeted verifier ✓

## Finding status

- `src/codemie/rest_api/routers/assistant.py:1166` — [security] CR-001 **unresolved** — only a docstring documents the version-pinned resume gap as an accepted out-of-scope limitation; `_ask_assistant_resume` still never computes `_effective_hide_tool_outputs` for a pinned execution context. A decision-to-accept does not resolve a `kind:code` finding.
- `src/codemie/rest_api/routers/conversation.py:281` — [other: spec-compliance] CR-002 **unresolved** — no `fix_evidence` entry was supplied for this id at all; the workflow-conversation filtering exemption question is still open.

## Checked and clean

commit-format ✓ · code-quality ✓ (stale audit finding does not reproduce) · 8 round-1 findings previously confirmed fixed — see code-review-final.json
