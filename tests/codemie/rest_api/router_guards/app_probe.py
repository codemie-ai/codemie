# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Child process of the router guards: build the real app once and dump what the guards compare.

Run as ``python app_probe.py <output.json>`` in a fresh interpreter, never imported: ``main.py``
registers Prometheus collectors and other process-global state at import, so it cannot be reloaded
in a test process. The environment comes from ``guarded_app.PROFILE_ENV``; this script only fakes the
collaborators ``main.py`` would otherwise reach at import time.

The output file holds ``openapi`` (``app.openapi()`` as JSON), ``routes`` (``{path, methods}`` in
``app.routes`` order) and ``contacts_attempted`` (every socket connect, which must stay empty).
"""

from __future__ import annotations

import json
import socket
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# Same optional native packages tests/conftest.py stubs when they are not installed.
_OPTIONAL_NATIVE_PACKAGES = ("tree_sitter_languages", "python_calamine", "switchyard", "switchyard.libsy")

contacts_attempted: list[str] = []


def _stub_missing_native_packages() -> None:
    for package in _OPTIONAL_NATIVE_PACKAGES:
        try:
            __import__(package)
        except ImportError:
            sys.modules[package] = MagicMock()


def _refuse_connection(_sock: socket.socket, address: Any) -> None:
    contacts_attempted.append(repr(address))
    raise ConnectionRefusedError(f"app_probe refuses outbound connections: {address!r}")


def _mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.__enter__ = MagicMock(return_value=engine)
    engine.__exit__ = MagicMock(return_value=False)
    return engine


def _import_app() -> Any:
    """Import ``codemie.rest_api.main`` with every outbound collaborator neutralized."""
    with ExitStack() as stack:
        # Developer .env files must not leak into the profile (process environment wins over them anyway).
        stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        stack.enter_context(patch("pydantic_settings.sources.DotEnvSettingsSource._read_env_files", return_value={}))
        stack.enter_context(patch("socket.socket.connect", _refuse_connection))
        stack.enter_context(patch("socket.socket.connect_ex", _refuse_connection))
        stack.enter_context(patch("codemie.clients.postgres.PostgresClient.get_engine", return_value=_mock_engine()))
        stack.enter_context(patch("codemie.rest_api.utils.state_import.StateImportService.import_indexes"))
        # main.py from-imports is_litellm_enabled, so it has to be patched before main is imported.
        stack.enter_context(patch("codemie.enterprise.litellm.is_litellm_enabled", return_value=True))
        import codemie.rest_api.main as main

    return main.app


def _route_table(app: Any) -> list[dict[str, Any]]:
    return [
        {"path": route.path, "methods": sorted(route.methods)}
        for route in app.routes
        if getattr(route, "methods", None)
    ]


def main(output_path: str) -> None:
    _stub_missing_native_packages()
    from fastapi.encoders import jsonable_encoder

    app = _import_app()
    dump = {
        "openapi": jsonable_encoder(app.openapi()),
        "routes": _route_table(app),
        "contacts_attempted": contacts_attempted,
    }
    Path(output_path).write_text(json.dumps(dump), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
