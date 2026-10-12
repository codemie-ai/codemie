DONE_WITH_CONCERNS
commit: f686aa9b9
changed: src/codemie_tools/ms_exchange/{enrichment.py(new),tools.py,tools_vars.py,toolkit.py}, tests/codemie_tools/ms_exchange/{test_enrichment.py(new),test_tools.py}
test: poetry run pytest tests/codemie_tools/ms_exchange/  (160 passed)
concern: ocr_model_factory (name -> chat model) is an injectable field on EnrichBodyTool; host wiring in src/codemie (toolkit_service pattern) is NOT done, so ocr_model is ignored until a factory is injected (falls back to chat_model).
