# Deferred from code review — 2026-09-29-a2a-server-protocol (2026-09-30)

- **Client BedrockAdapter hardcodes old methods** — `src/codemie/rest_api/a2a/client/adapter.py:86` — make_streaming_request still passes method="message/stream", now failing validation; client tests fail. Pre-existing: client is slice 3 scope per the task intent.
- **Legacy methods and agent.json removed without compat** — `src/codemie/rest_api/routers/a2a.py` — old method names, `type` parts and discovery route no longer accepted. Pre-existing: backward-compat is slice 4 scope per the task intent.
- **A2A-Version header not negotiated** — `src/codemie/rest_api/a2a/version.py` — header never checked. Pre-existing: header negotiation is a stated non-goal of this slice's design.
