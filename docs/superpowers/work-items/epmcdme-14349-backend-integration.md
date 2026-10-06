---
name: epmcdme-14349-backend-integration
title: "EPMCDME-14349: UI-Backend Integration for Project Settings Models Tab"
description: Verify and validate backend APIs for new UI Project Settings Models Tab feature
status: ready
external_ticket: "EPMCDME-14349"
---

# Work Item: EPMCDME-14349 — UI-Backend Integration

**Created:** 2026-09-17  
**Status:** Ready for Implementation  
**Priority:** High (blocks UI deployment)  
**Type:** Integration / Validation  

## Objective

Verify that existing backend APIs work correctly with the new Project Settings Models Tab UI feature. Establish baseline API contracts and document optional backend persistence design for future cross-device sync capability.

## Context

The UI team has implemented a new tab-based Project Settings page with a Models tab that allows users to enable/disable models and set defaults per project. Model configuration is stored in browser localStorage (no backend persistence in MVP). This work item ensures backend APIs are functioning correctly and documents the migration path for optional backend persistence.

## Acceptance Criteria

- [ ] **API Verification:** All three required backend APIs respond correctly to requests with proper HTTP 200 status and valid JSON responses
  - `GET /v1/llm_models` returns model list with `value`, `label`, `isPremium` fields
  - `GET /v1/projects/{name}` returns project details including `name`, `description`, `user_count`, `admin_count`
  - `GET /v1/projects/{name}/budgets` returns budget list
- [ ] **No Breaking Changes:** Verify that existing API response schemas remain unchanged
- [ ] **Integration Test Passes:** Run end-to-end test scenario comparing UI expectations with API responses
- [ ] **Optional Persistence Designed:** Document complete backend persistence architecture for future implementation
- [ ] **Verification Artifacts:** `verify_integration.py` script passes all tests

## Out of Scope (This Sprint)

- Implementing backend persistence endpoints
- Audit logging for model configuration changes
- Database schema migrations
- Admin enforcement of global model restrictions

## Related Artifacts

- **Integration Plan:** `UI_BACKEND_INTEGRATION_PLAN.md` (9 sections)
- **Integration Summary:** `INTEGRATION_SUMMARY.md`
- **Quick Reference:** `QUICK_REFERENCE.md`
- **Verification Script:** `verify_integration.py`

## Technical Details

### Required Backend APIs (No Changes Needed)

| API | Purpose | Backend Code |
|-----|---------|--------------|
| `GET /v1/llm_models` | Load available models | `src/codemie/service/llm_service/llm_service.py:51` |
| `GET /v1/projects/{name}` | Load project details | `src/codemie/rest_api/routers/projects.py` |
| `GET /v1/projects/{name}/budgets` | Load project budgets | `src/codemie/rest_api/routers/projects.py` |

### Frontend Router Changes Required

Two new routes must be added to UI router configuration:
```typescript
{ name: 'projects-management-models', path: ':projectName/models' }
{ name: 'projects-management-integrations', path: ':projectName/integrations' }
```

### Optional Future: Backend Persistence Architecture

**Endpoints to Design (Not Implement This Sprint):**
- `GET /v1/projects/{projectName}/model-configuration`
- `PUT /v1/projects/{projectName}/model-configuration`

**Database Table:**
```sql
CREATE TABLE project_model_configuration (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id),
    enabled_model_ids TEXT[] NOT NULL DEFAULT '{}',
    default_model_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    updated_by_user_id UUID REFERENCES users(id),
    UNIQUE(project_id)
);
```

See Section 3 of `UI_BACKEND_INTEGRATION_PLAN.md` for complete design details.

## Testing Strategy

### Phase 1: API Verification (Automated)
Run provided verification script:
```bash
python verify_integration.py \
  --base-url http://localhost:8000 \
  --token <your-bearer-token>
```
Expected result: ✅ All tests passed

### Phase 2: End-to-End Test (Manual)
1. Navigate to project settings page
2. Verify all 4 tabs render: Overview, Members, Models, Integrations
3. Overview tab: Load project info from API
4. Models tab: Load models from API, toggle on/off, set default, refresh → persists
5. Members tab: Load budgets from API
6. Switch projects: Model config isolated per project
7. No console errors

### Phase 3: Response Validation
Compare actual API responses against UI expectations in `UI_BACKEND_INTEGRATION_PLAN.md` Section 2.

## Deployment Impact

- **Impact Level:** Low
- **Breaking Changes:** None (uses existing APIs)
- **Database Changes:** None
- **Configuration Changes:** None
- **UI Deployment Blocker:** No (but verification confirms readiness)

## Success Criteria Summary

✅ All 3 backend APIs operational and responding correctly  
✅ No breaking changes to existing API contracts  
✅ End-to-end test passes without console errors  
✅ Optional persistence architecture documented  
✅ Verification script passes all tests  

## History

- **2026-09-17:** Work item created based on UI-Backend integration plan
- **Status:** Ready for intake into requirements phase

---

**Linked Artifacts:** 
- UI_BACKEND_INTEGRATION_PLAN.md
- INTEGRATION_SUMMARY.md
- verify_integration.py
