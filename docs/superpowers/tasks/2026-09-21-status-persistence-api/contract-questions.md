# Contract decisions this spec must pin down

**Status**: OPEN — to be resolved during Stage 3 brainstorming.
**Why this file exists**: two frontend sessions are paused waiting on these answers. They are
mandatory `spec.md` content, not questions to defer to the plan stage. Written down so a session
restart does not lose them.

**Slice**: `status-persistence-api` (backend) of EPMCDME-15077 · BE sub-task **EPMCDME-15080**
**Siblings, in `../codemie-ui`**: `admin-ui` (admin page) and `catalog-status-surfacing`
(`/help/models` + consumers). Both depend on the answers below.

---

## The five cross-team contract decisions

### 1. Status enum wire values
What exactly goes over JSON. `enabled`/`disabled`/`deprecated` vs `ENABLED`/`DISABLED`/`DEPRECATED`
vs `enable`/`disable`/`deprecated`. FE will switch on these strings.

### 2. Model identity key
What the status row is keyed on and addressed by: `base_name`, `deployment_name`, or a composite.
`LLMModel` carries both; `deployment_name` is the provider-specific one. FE needs to know which value
to send when setting status.

### 3. Admin endpoint paths and request/response shapes
Full shapes for both read and set. Follow existing `admin.py` conventions: `/v1/admin/...` prefix,
`Depends(admin_access_only)`, `response_model=BaseResponse` / `BaseResponseWithData`, and log
`admin.id` / `admin.name` on every action. State whether setting is single-model, bulk, or both.

### 4. Does status appear on the public `GET /v1/llm_models` payload?
And if so, under exactly what field name. **This gates whether the `catalog-status-surfacing` slice
can read it at all.** Note `llm_models.py` endpoints are `authenticate`-only with no admin scoping,
and are consumed by `/help/models`, the chat/assistant `LLMSelector` family, and project-level model
pickers.

### 5. Runtime semantics of `Deprecated`
The biggest open question — no analog exists in the codebase today. Answer each part:
- Does a deprecated model still appear in `get_allowed_chat_models` /
  `get_allowed_embedding_models` / `get_allowed_router_options` /
  `get_allowed_image_generation_models`?
- Can a user still **select** it for a new assistant/chat/workflow?
- Do **existing** entities already referencing it keep working?
- Net effect: merely flagged in the UI, hidden for new use but honored for existing use, or fully
  blocking?

`llm_retirement_service.py` is a **different** concept — a one-time bulk rewrite of model-name
references across `assistants`, `assistant_configurations`, `workflows` rows. Do not conflate
"deprecated status" with "retirement". State explicitly how the two relate.

---

## Two further design-critical decisions (backend-internal, but correctness-critical)

### 6. Merge with the provider-sourced `enabled` boolean
`LLMModel.enabled` comes from the live LiteLLM/DIAL `model_info.enabled`, **not** from an admin
action. Reusing that field for admin "Disable" risks silent overwrite on the next
`GET /v1/admin/llm/reload`. Decide whether admin status is a separate field, and resolve the conflict
matrix: provider says `enabled=false` but admin says enabled, and vice versa — which wins?

### 7. Lifecycle edges
- What happens to a persisted status row when the model **disappears** from the provider catalog?
- What is the **default** status for a model that appears in the catalog with no persisted row?

---

## Handoff

Once the spec is approved, these answers get lifted verbatim into `contract.md` in both frontend task
dirs:
- `../codemie-ui/docs/superpowers/tasks/2026-09-21-admin-ui/contract.md`
- `../codemie-ui-catalog-status/docs/superpowers/tasks/2026-09-21-catalog-status-surfacing/contract.md`
