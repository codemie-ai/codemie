# GitLab Merge Request Webhook Filters - Technical Analysis

**Date:** 2026-07-13  
**Task:** Implement MR event filtering for GitLab webhooks  
**Scope:** Webhook infrastructure, event routing, UI configuration, data models

---

## Executive Summary

The CodeMie webhook infrastructure currently supports **GitHub-specific event filtering** (via `github_event_filter` configuration) but has **no GitLab-specific merge request event filtering**. The architecture is extensible but requires changes across three layers:

1. **Backend:** Event filtering logic (currently GitHub-only)
2. **UI:** Configuration fields for GitLab MR event types
3. **Database:** Storage schema for GitLab-specific filter configurations

**Risk Level:** Medium - Changes touch core webhook dispatch, security verification, and UI configuration layers, but the existing GitHub pattern provides a clear precedent.

---

## Codebase Findings

### Current Webhook Architecture

#### 1. Webhook Dispatch Entry Point
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/rest_api/routers/webhook.py`

- **Route:** `POST /v1/webhooks/{webhook_id}`
- **Rate limiting:** Enforced before handler execution
- **Flow:** Request body → WebhookService.invoke_webhook_logic()
- **Error handling:** HTTPException with detailed error messages and metrics

Key observation: The router is **provider-agnostic** — all logic is delegated to WebhookService.

#### 2. Core Webhook Service
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/triggers/bindings/webhook.py` (489 lines)

**Architecture Overview:**
- Retrieves webhook configuration from Settings using webhook_id
- Verifies security (GitHub signature OR legacy header)
- Routes to resource handlers (workflow, assistant, datasource)
- Sends metrics on success/failure

**Security Verification Layers (Priority Order):**
1. GitHub signature verification (HMAC-SHA256 or SHA-1 fallback) with event filtering
2. Legacy header-based authentication
3. No security fallback (with warning)

**Event Filtering - GitHub Only:**
```python
event_filter = setting.credential(cls.GITHUB_EVENT_FILTER)
if event_filter:
    allowed_events = [e.strip() for e in event_filter.split(',') if e.strip()]
    GitHubWebhookSecurity.validate_event_type(request, allowed_events)
```

Lines 272-275 in webhook.py show filtering only happens for GitHub webhooks after signature verification.

**Current Configuration Storage:**
- Webhook settings stored in Settings table via credential_values (key-value pairs)
- Keys like: `webhook_id`, `is_enabled`, `github_webhook_secret`, `github_event_filter`, `secure_header_name`, `secure_header_value`, `resource_type`, `resource_id`
- **No GitLab-specific keys currently exist**

#### 3. GitHub Security Module
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/triggers/bindings/github_webhook_security.py` (257 lines)

**Key Methods:**
- `is_github_webhook()` — Detects GitHub webhooks via headers (X-Hub-Signature, X-GitHub-Event, GitHub-Hookshot user agent)
- `verify_signature()` — SHA-256/SHA-1 HMAC verification
- `validate_event_type()` — Checks if event matches allowed list (lines 207-238)
- `extract_github_metadata()` — Pulls event type, delivery ID, hook ID from headers

**Pattern to Follow:** GitHub event validation is header-based (`X-GitHub-Event` header) and uses simple CSV filtering (`'pull_request,push'`).

### WebSocket/Header Constants
- GitHub uses: `X-GitHub-Event`, `X-GitHub-Delivery`, `X-GitHub-Hook-ID`, `X-Hub-Signature-256`, `X-Hub-Signature`
- **GitLab uses:** `X-Gitlab-Event`, `X-Gitlab-Delivery`, `X-Gitlab-Token` (the configured secret sent **verbatim in plaintext** — NOT an HMAC signature of the body, unlike GitHub), `User-Agent: GitLab/...`
- **GitLab MR event types:** `merge_request` (with action: `open`, `close`, `merge`, `update`, etc.)

### Settings Model & Storage
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/rest_api/models/settings.py`

- Settings are key-value credential pairs stored in JSONB (`credential_values: List[CredentialValues]`)
- `normalize_values()` method converts to dict
- `credential(key)` method returns single value
- **No schema migration needed** — JSONB is schema-less

**Webhook-specific retrieval:**
```python
query = {"credential_values.key.keyword": "webhook_id", "credential_values.value.keyword": webhook_id}
setting = SettingsService.retrieve_setting(query)
```

### UI Configuration Layer
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie-ui/src/utils/settingsUIConfig.ts` (lines 730-785)

**Current Webhook Configuration:**
```typescript
webhook: {
  roleRestrictionType: CredentialRoleRestriction.ADMIN_ONLY,
  fields: {
    webhook_id: { ... },
    is_enabled: { ... },
    secure_header_name: { ... },
    secure_header_value: { ... },
    github_require_sha256: { ... },
    github_webhook_secret: { ... },
    github_event_filter: { ... },  // GitHub-specific
    resource_type: { select: ['assistant', 'workflow', 'datasource'] },
    resource_id: { ... },
  }
}
```

**Key Observation:** All current filtering is GitHub-specific. **No GitLab event filter field exists.**

### Workflow Execution Trigger
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/triggers/actors/workflow.py`

- Webhook triggers workflows via `invoke_workflow()` async function
- Takes raw payload but **doesn't parse or filter** it
- Forwards to workflow execution endpoint with user_input = raw payload
- **No payload inspection for event types** happens here

**Implication:** Filtering must happen at webhook security/dispatch layer, not at workflow invocation.

### Monitoring & Metrics
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/service/monitoring/webhook_monitoring_service.py`

- Sends metrics for invocation success/failure
- Metric attributes include: webhook_id, project, user_id, resource_type, resource_id, webhook_alias, status
- **No event-type-specific metrics currently recorded**

---

## Implementation Sites

### Backend Changes Required

#### 1. GitLab Security Module (NEW)
**Create:** `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/triggers/bindings/gitlab_webhook_security.py`

**Responsibilities:**
- Detect GitLab webhooks (check for `X-Gitlab-Event` header, GitLab user agent)
- Verify GitLab token signature (token comparison, similar to legacy header auth)
- Validate event type (extract from `X-Gitlab-Event` header)
- Extract GitLab metadata (event type, delivery ID, project ID from headers)

**Pattern:** Mirror `github_webhook_security.py` structure

#### 2. WebhookService Enhancement
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/triggers/bindings/webhook.py`

**Changes Needed:**

1. **Add GitLab event filter configuration constant:**
   ```python
   GITLAB_WEBHOOK_TOKEN = "gitlab_webhook_token"
   GITLAB_EVENT_FILTER = "gitlab_event_filter"
   ```

2. **Extend verify_security_header():**
   - After GitHub verification, check for GitLab webhook
   - Call GitLabWebhookSecurity methods similar to GitHub pattern
   - Lines ~230: Add priority 1.5 for GitLab signature verification

3. **Import GitLab security module** at top of file

4. **Update event filter validation:**
   - Currently only GitHub checks event_filter (line 272-275)
   - Add parallel GitLab event filtering after GitLab verification

#### 3. Trigger Models
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/triggers/trigger_models.py`

**Status:** No changes needed — generic ReindexTaskPayload already supports both GitHub and GitLab payloads

### UI Configuration Changes

#### 1. Settings UI Configuration
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie-ui/src/utils/settingsUIConfig.ts`

**Changes Needed (lines 730-785):**

```typescript
webhook: {
  // ... existing fields ...
  github_event_filter: {
    label: 'GitHub Event Filter',
    placeholder: 'Optional field, comma-separated events, e.g. "pull_request,push"',
  },
  gitlab_webhook_token: {
    label: 'GitLab Webhook Secret Token',
    placeholder: 'Optional field',
    sensitive: true,
    shouldShow: (values) => isGitLabWebhook(values),  // NEW conditional
  },
  gitlab_event_filter: {
    label: 'GitLab Event Filter',
    placeholder: 'Optional field, comma-separated MR actions, e.g. "open,close,merge"',
    shouldShow: (values) => isGitLabWebhook(values),  // NEW conditional
  },
}
```

**Helper Functions to Add:**
```typescript
const isGitLabWebhook = (values) => {
  // Detect based on resource_type or explicit GitLab indicator
  // Could be based on datasource URL pattern or new field
}
```

**User Experience Impact:**
- Show/hide GitLab fields conditionally
- Users don't see GitHub fields when using GitLab webhooks

#### 2. CredentialFields Component
**File:** `/Users/oleg_sotnichenko/codemie-dev/codemie-ui/src/pages/integrations/components/SettingsForm/CredentialFields.tsx`

**Status:** No changes needed — component already supports conditional `shouldShow` rendering

### Database Schema

**Status:** NO MIGRATION NEEDED

- Settings uses JSONB credential_values (schema-less)
- New keys (gitlab_webhook_token, gitlab_event_filter) will be stored as standard key-value pairs
- Backward compatible — existing GitHub webhooks unaffected

---

## Risk Indicators & Architectural Gaps

### High-Risk Areas

1. **Security Verification Complexity (Medium Risk)**
   - Currently GitHub signature is priority 1, legacy header is priority 2
   - Need to determine GitLab priority (signature token vs header auth)
   - **Mitigation:** Follow GitHub precedent, add comprehensive tests for each provider

2. **Event Type Payload Variation (Medium Risk)**
   - GitHub event type in header (`X-GitHub-Event`)
   - GitLab event type in header (`X-Gitlab-Event`)
   - GitLab also includes action in payload (e.g., `merge_request:open`)
   - **Mitigation:** Parse both header AND payload for robustness

3. **Webhook Detection Logic (Low-Medium Risk)**
   - Currently: GitHub detection uses header + user agent checks
   - GitLab detection will need similar header checks
   - **Mitigation:** Add explicit provider hints in settings if ambiguity arises

### Architectural Gaps

1. **No Provider Abstraction**
   - Each provider has its own security module (currently GitHub only)
   - Consider creating `WebhookProvider` base class for future extensibility
   - **Current Impact:** Medium — code duplication across providers

2. **Event Payload Not Passed to Handlers**
   - Workflow/Assistant triggers receive raw payload but don't parse event types
   - Filtering happens only at security layer (good design)
   - **Current Impact:** Low — separation of concerns is clean

3. **No Event-Type-Specific Routing**
   - All events for a webhook target same resource
   - Cannot route different event types to different workflows
   - **Current Impact:** Medium-High — limitation for complex automation

4. **Limited Filtering Capabilities**
   - Only event type filtering supported
   - Cannot filter by PR branch, author, labels, etc.
   - **Current Impact:** Medium — reasonable MVP, extensible later

### Extensibility Considerations

- **Future Providers:** Bitbucket, GitLab self-hosted, Gitea would follow same pattern
- **Future Filtering:** Could extend to payload-based filtering (branch names, labels, authors)
- **Future Routing:** Could support multiple workflows per webhook with different event filters

---

## Implementation Checklist

### Phase 1: Backend (No UI changes needed for MVP)
- [ ] Create `gitlab_webhook_security.py` module
- [ ] Add GitLab constants to WebhookService
- [ ] Implement GitLab detection and signature verification
- [ ] Add GitLab event type validation
- [ ] Update security verification priority logic
- [ ] Add unit tests for GitLab webhook flow
- [ ] Add integration tests for GitHub vs GitLab differentiation

### Phase 2: UI Enhancement (Optional for Phase 1)
- [ ] Add `gitlab_event_filter` field to settingsUIConfig
- [ ] Add conditional visibility for GitLab fields
- [ ] Add `gitlab_webhook_token` field (if using token-based auth)
- [ ] Update webhook creation form to handle GitLab inputs

### Phase 3: Testing & Documentation
- [ ] Create test fixtures for GitLab MR payloads (open, merge, close, update)
- [ ] Test filter matching for multiple event types
- [ ] Test security verification (valid/invalid tokens)
- [ ] Update webhook configuration documentation
- [ ] Add GitLab webhook setup guide

---

## Critical Files Summary

| File | Location | Purpose | Complexity |
|---|---|---|---|
| webhook.py | `/triggers/bindings/webhook.py` | Core webhook dispatch | HIGH |
| github_webhook_security.py | `/triggers/bindings/github_webhook_security.py` | GitHub signature verification | MEDIUM |
| gitlab_webhook_security.py | `/triggers/bindings/gitlab_webhook_security.py` (NEW) | GitLab signature verification | MEDIUM |
| settingsUIConfig.ts | `/codemie-ui/src/utils/settingsUIConfig.ts` | UI field configuration | LOW |
| settings.py | `/rest_api/models/settings.py` | Settings storage model | LOW |
| webhook.py | `/rest_api/routers/webhook.py` | HTTP endpoint | LOW |

---

## Related Systems

- **Monitoring:** WebhookMonitoringService already supports generic attributes
- **Settings Service:** Already handles GitHub-specific fields, will handle GitLab identically
- **Workflow Execution:** invoke_workflow() doesn't need changes
- **Rate Limiting:** webhook_rate_limiter.py doesn't need provider-specific changes
- **Authentication:** Security patterns already established in security-patterns.md guide

---

## GitLab Event Types Reference

**MR Event Header:** `X-Gitlab-Event: Merge Request Hook`  
**MR Event Actions (in payload):** open, close, merge, update, reopen, approved, unapproved, etc.

**Filtering Example:** `gitlab_event_filter: "open,merge"` → Only trigger on open and merge actions

---

## Open Questions / Decision Points

1. **Should GitLab use token verification or header auth?**
   - Current GitHub implementation uses both (priority order)
   - GitLab webhooks typically use token (similar to GitHub secret)
   - **Recommendation:** Implement token verification as priority 1, fallback to header auth

2. **How to detect provider at webhook time?**
   - Current code only looks at headers to identify GitHub
   - For GitLab, look for `X-Gitlab-Event` and `X-Gitlab-Delivery`
   - **Decision:** Header-based detection is sufficient

3. **Should users be able to configure both GitHub and GitLab for same webhook?**
   - Current design: One webhook per provider
   - Multiple providers would require separate webhook IDs
   - **Recommendation:** Maintain current design (one provider per webhook)

4. **How to test GitLab integration?**
   - Need sample GitLab MR payloads
   - Can use GitLab webhook simulation or recorded payloads
   - **Action:** Add fixtures with real GitLab payload examples
