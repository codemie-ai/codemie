# Code Review Brief: EPMCDME-14797 (Backend)

Prevent backend generation aborts on passive socket drops, allow explicit cancellations, and guarantee zero stuck turns via anti-hang exception recovery.

### Key Changes
- Implemented `GenerationManager` to register and track active generators.
- Intercepted `GeneratorExit` to drain remaining chunks and finalize responses asynchronously in thread pool.
- Persisted prompt inception with `in_progress=True` upon endpoint invocation.
- Exposed explicit `POST /v1/conversations/{conversation_id}/abort` endpoint.
- Gated metric calculation and automated chat naming behind `if not in_progress:`.
- Added anti-hang exception recovery in `_serve_data`, `_handle_stream`, and drainer to persist `status=ERROR` and `in_progress=False`.
- Cleaned generation registry on stream start failures.
- Preserved `INTERRUPTED` status idempotency in `ConversationService.upsert_chat_history`.
