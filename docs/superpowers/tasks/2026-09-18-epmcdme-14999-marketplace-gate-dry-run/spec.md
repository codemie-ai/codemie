# Spec: Marketplace Gate Dry-Run Endpoint

**Ticket:** EPMCDME-14999  
**Branch:** EPMCDME-14234_marketplace-management  
**Date:** 2026-09-18

## Problem

When a user edits an APPROVED or PENDING_REVIEW assistant and clicks Save, the FE needs to validate unsaved form values against the deterministic marketplace gates *before* writing to the DB. The existing `POST …/publish` runs gates against persisted state and has side effects. A side-effect-free endpoint is needed.

## Solution

Add `POST /v1/assistants/{id}/marketplace/publish/gate-check` — a read-only endpoint that runs the existing gate engine against request body values and returns the findings. No DB writes, no queue mutations, no status changes.

## Endpoint

- **Path:** `POST /v1/assistants/{assistant_id}/marketplace/publish/gate-check`
- **Auth:** Standard bearer token (write access to the assistant)
- **Side effects:** None

### Request Body (`GateCheckRequest`)

All fields from `CreateAssistantRequest` plus `categories` (required, list[str]). Returns 422 if `categories` is missing.

### Response

| Status | Body |
|--------|------|
| 200 | `{"passed": true, "gate_findings": []}` — all gates passed (findings may be non-empty for non-CRITICAL findings) |
| 422 | CRITICAL gate failures: same `ExtendedHTTPException` structure as `POST …/publish` (`error.message`, `error.details.assistant_id`, `error.details.requires_confirmation`, `error.details.failed_rules`, `error.details.gate_findings`). Also returned for missing `categories` (Pydantic validation error). |
| 404 | Assistant not found |
| 401 | Insufficient permissions (write access required) |

CRITICAL findings raise 422 — matching the publish endpoint error structure — so the FE can handle both endpoints identically. Non-CRITICAL findings are returned in `gate_findings` on a 200 response.

## Implementation

- New `GateCheckRequest` model: extends `CreateAssistantRequest`, overrides `categories` as `list[str]` (required)
- New `GateCheckResponse` model: `passed: bool`, `gate_findings: list[GateFinding]`
- Handler loads assistant from DB, overrides gate-relevant fields (`name`, `description`, `system_prompt`, `categories`) with request values, calls `_publish_gate_findings(assistant)`, returns response
- Route registered **before** `POST …/publish` to avoid Starlette path conflicts

## Out of Scope

- No changes to `POST …/publish`, `PUT /v1/assistants/{id}`, or `POST …/unpublish`
- No advisory quality-validation LLM review
- No sub-assistant gate checks
- No `force_publish` or `ignore_recommendations` flags
