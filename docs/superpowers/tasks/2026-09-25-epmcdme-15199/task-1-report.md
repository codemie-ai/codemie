DONE
commit: 341185fe3
changed: src/codemie_tools/base/models.py, src/codemie_tools/ms_exchange/__init__.py, src/codemie_tools/ms_exchange/toolkit.py, tests/codemie_tools/ms_exchange/test_toolkit.py
test_command: cd /Users/Oleksandr_Cherevach/work/codemie-dev/codemie && poetry run pytest tests/codemie_tools/ms_exchange/test_toolkit.py tests/codemie_tools/base/test_toolkit_provider.py -v
RED evidence: initial run failed collection with ModuleNotFoundError: No module named 'codemie_tools.ms_exchange' (ExchangeToolkit/ExchangeToolkitUI import), i.e. the toolkit did not exist yet.
GREEN evidence: 26 passed, 0 failed, 0 errors (6 new ms_exchange tests + 20 existing toolkit_provider tests, no regressions).
Renames: none.
Reused pattern: mirrored src/codemie_tools/itsm/toolkit.py (DiscoverableToolkit.get_definition returning a ToolKit subclass) and src/codemie_tools/sharepoint/__init__.py (license-header-only __init__.py); reused the lru_cache-clear pattern from tests/codemie_tools/base/test_toolkit_provider.py.
Guide applied: none consulted beyond AGENTS.md routing (Agents & Tools category); no protocol code, no new deps, tools=[] per plan constraints.
Concerns: none. The get_tools_info AC2 test patches codemie.enterprise.plugin.get_plugin_toolkit_ui_info (source module, since tools_info_service imports it locally inside the function) and ProviderToolkitsFactory.get_toolkits to simulate empty enterprise/provider sources, as specified. That test and the toolkit tests all live in the single declared test file tests/codemie_tools/ms_exchange/test_toolkit.py, per the task's Files list (only one test file was declared).
