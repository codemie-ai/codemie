DONE
Commit: 754c88539
Changed: src/codemie/service/settings/settings_tester.py, tests/codemie/service/settings/test_settings_tester.py
Test: poetry run pytest tests/codemie/service/settings/test_settings_tester.py (RED 15 failed, GREEN 45 passed); ruff check/format clean.
Notes: handler catches ValidationError (fixed sentence), ExchangeError (exc.MESSAGE), any other Exception (InternalError.MESSAGE). Masked creds resolved by existing __init__ logic (tested).
