DONE

Commit: 12dc4ff6b
Changed: src/codemie_tools/ms_exchange/client_base.py, src/codemie_tools/ms_exchange/graph_client.py, tests/codemie_tools/ms_exchange/test_graph_client.py
Test: poetry run pytest tests/codemie_tools/ms_exchange/test_graph_client.py (12 passed; RED first was ImportError, graph_client missing). Full ms_exchange dir: 108 passed. ruff check/format clean.

Return shapes (documented in client_base.py; EWS must match):
- Folder: {id, name, total_count, unread_count}
- Message summary: {id, subject, from ("Name <addr>"), received (ISO str|None), is_read, has_attachments}
- Message: summary + to[], cc[], body (plain text)
- Attachment: {id, name, content_type, size, is_file}
- get_attachment_bytes -> bytes; get_text_body -> str; check_inbox -> None
- client_base.MAX_PAGE = 50; ExchangeClient Protocol.
Notes: build_graph_client returns the raw GraphServiceClient; GraphExchangeClient(config) calls it (patchable). run_sync is the loop-detect/thread/asyncio.run bridge with REQUEST_TIMEOUT_SECONDS=60.
