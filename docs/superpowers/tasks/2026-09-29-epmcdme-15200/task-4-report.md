DONE
commit: 9f07263b9
changed: src/codemie_tools/ms_exchange/ews_client.py, src/codemie_tools/ms_exchange/client_factory.py, tests/codemie_tools/ms_exchange/test_ews_client.py, tests/codemie_tools/ms_exchange/test_client_factory.py
test: poetry run pytest tests/codemie_tools/ms_exchange/  (124 passed; ruff check/format clean)
client_factory public names: get_client(config)
ews_client public names: build_ews_client(config) -> exchangelib Account, EwsExchangeClient, REQUEST_TIMEOUT_SECONDS, INBOX
Notes: bare host -> Configuration(server=), full URL -> service_endpoint (server= with a URL would build a broken endpoint). 'id' is not an EWS sortable field, so ties are ordered by id only within a window.
