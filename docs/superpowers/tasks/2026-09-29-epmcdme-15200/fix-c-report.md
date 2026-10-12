DONE
Commit: 9b72ab498 (single combined commit)
Changed: src/codemie/service/tools/toolkit_service.py, src/codemie/service/tools/tool_service.py, src/codemie_tools/ms_exchange/{enrichment,ews_client,tools}.py, tests: tests/codemie/service/tools/test_toolkit_service.py, tests/codemie_tools/ms_exchange/{test_enrichment,test_ews_client,test_tools}.py
1. OCR factory: fixed. Factory now built before and independent of the chat_model block (also used in the fallback construction). Workflow path (ToolsService.find_tool_from_config -> get_core_tools, no llm_model) now receives it; request_uuid=execution_id passed there. Tests: no llm_model, and chat_model injection failing.
2. Size caps: fixed. enrich accepts optional 4th tuple element (declared size); per-attachment and running-total caps checked before fetch; post-fetch check kept; unknown size fetches then checks. EnrichBodyTool passes size. Tests at enrichment and tool level (content never fetched).
3. EWS host policy: fixed. localhost/*.localhost, loopback, link-local, unspecified, private, reserved, multicast IP literals (IPv4, IPv6, bracketed, v4-mapped) raise BadEndpointError with the fixed message; bare hosts too; tests assert no host in message.
Test: poetry run pytest tests/codemie_tools/ms_exchange/ tests/codemie/service/tools/test_toolkit_service.py -> EXIT=0, 292 passed (log: test.local.log). Also tests/codemie/service/tools + tests/codemie/workflows passed. ruff check/format clean.
Not done: none. Note: workflow tools have no chat_model (no llm_model available there); OCR there works only with ocr_model set.
