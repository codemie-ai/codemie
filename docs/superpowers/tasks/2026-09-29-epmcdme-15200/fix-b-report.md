DONE
commit: a23a5b673
changed: src/codemie/service/tools/toolkit_service.py, tests/codemie/service/tools/test_toolkit_service.py
test: poetry run pytest tests/codemie/service/tools/test_toolkit_service.py tests/codemie_tools/ms_exchange/ (260 passed); ruff check/format clean
notes: _initialize_tool is the single site injecting chat_model for configured tools (assistants and workflows both go through it); it now also passes ocr_model_factory when the tool declares that field.
