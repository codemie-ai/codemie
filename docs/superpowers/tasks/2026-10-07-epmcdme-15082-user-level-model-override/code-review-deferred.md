# Deferred from code review — 2026-10-07-epmcdme-15082-user-level-model-override (2026-10-13)

- **Router options not narrowed by user override in listing** — `src/codemie/rest_api/routers/llm_models.py:85` — `get_allowed_router_options` is appended without override filtering. Pre-existing: router options were never narrowed by the project whitelist either, and the spec is silent on them; execution still goes through user-scoped resolution.
- **ContextVar propagation into non-copying threads unconfirmed** — `src/codemie/workflows/nodes/agent_node.py:130` — enforcement relies on `user_model_context`; `RouterChatModel._generate` copies context, but other executors were not traced. Pre-existing execution model; worth a Stage 7 end-to-end check.
