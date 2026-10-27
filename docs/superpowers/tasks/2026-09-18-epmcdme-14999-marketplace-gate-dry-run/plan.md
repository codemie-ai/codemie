# Plan: Marketplace Gate Dry-Run Endpoint

**Spec:** spec.md  
**Ticket:** EPMCDME-14999

## Tasks

### T1: Add request/response models
- Add `GateCheckRequest` model (extends `CreateAssistantRequest`, `categories: list[str]` required)
- Add `GateCheckResponse` model (`passed: bool`, `gate_findings: list[GateFinding]`)
- Location: `src/codemie/rest_api/routers/assistant.py` (with other request/response models near top)
- Test-first: yes — test that `GateCheckRequest` rejects missing `categories` and accepts valid payload

### T2: Add handler and route
- Add `gate_check_assistant_for_marketplace` handler before `publish_assistant_to_marketplace` (line 1864)
- Handler: load assistant → check write access → override fields → call `_publish_gate_findings` → return `GateCheckResponse`
- CRITICAL findings → `passed: False`; no findings → `passed: True`; always 200
- Test-first: yes — test 200/pass, 200/fail-with-findings, 404, 403, 422-missing-categories
