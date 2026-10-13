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

"""The application the router guards inspect: ``main.py`` built under one fixed profile.

``load_guarded_app_dump`` runs ``app_probe.py`` in a fresh interpreter (``main.py`` cannot be reloaded
in-process, see ``test_main_retrieval_backend_disabled.py``) and returns its JSON dump. The child gets
a scrubbed environment: only ``PATH`` and ``HOME`` are inherited, everything else is ``PROFILE_ENV``
or a ``Config`` default, so neither the developer's shell nor ``.env`` files change the result.

Every conditional router of ``main.py`` is on except MCP auth: ``index`` (Elasticsearch configured),
``budget_router`` (LiteLLM plus ``LLM_PROXY_BUDGET_CHECK_ENABLED``), the user-management group and
``local_auth_router`` (``ENABLE_USER_MANAGEMENT`` with ``IDP_PROVIDER=local``) and
``POST /v1/workflows/generate``.

Recorded gap: the ``MCP_AUTH_ENABLED`` variants of ``get_mcp_auth_router()`` and ``get_cimd_router()``
are not guarded; the guarded app carries their 503 stubs. Those routers are registered last, outside the
packages being split, and the enabled variants exist only when the optional ``codemie-enterprise`` MCP auth
package is installed, so switching them on would make the profile depend on the developer's environment.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

_REPO_ROOT: Final = Path(__file__).resolve().parents[4]
_PROBE_SCRIPT: Final = Path(__file__).with_name("app_probe.py")
_PROBE_TIMEOUT_SECONDS: Final = 180
_INHERITED_ENV_NAMES: Final = ("PATH", "HOME")

# Values that switch routers on, plus every setting that reaches the OpenAPI schema, pinned to a
# constant (APP_VERSION so a release bump does not break the snapshot). LITELLM_LOCAL_MODEL_COST_MAP
# stops ``import litellm`` from downloading the model cost map.
PROFILE_ENV: Final[dict[str, str]] = {
    "ENV": "local",
    "ENABLE_USER_MANAGEMENT": "true",
    "IDP_PROVIDER": "local",
    "RETRIEVAL_BACKEND": "elasticsearch",
    "ELASTIC_URL": "http://localhost:9200",
    "LLM_PROXY_ENABLED": "true",
    "LLM_PROXY_BUDGET_CHECK_ENABLED": "true",
    "WORKFLOW_GENERATION_ENABLED": "true",
    "MCP_AUTH_ENABLED": "false",  # intentional gap, see the module docstring
    "MCP_AUTH_TMS_ENABLED": "false",
    "APP_VERSION": "0.0.0-router-guards",
    "API_ROOT_PATH": "",
    "PASSWORD_MIN_LENGTH": "12",
    "ANALYTICS_DEFAULT_PAGE_SIZE": "20",
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    "PYTHONHASHSEED": "0",
}


def _probe_environment() -> dict[str, str]:
    inherited = {name: os.environ[name] for name in _INHERITED_ENV_NAMES if name in os.environ}
    return {**inherited, "PYTHONPATH": str(_REPO_ROOT / "src"), **PROFILE_ENV}


def _as_text(stream: str | bytes | None) -> str:
    # TimeoutExpired carries bytes on POSIX even when the child ran in text mode.
    return stream.decode(errors="replace") if isinstance(stream, bytes) else stream or ""


def _run_probe() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as workdir:
        output_path = Path(workdir) / "app_dump.json"
        try:
            result = subprocess.run(
                [sys.executable, str(_PROBE_SCRIPT), str(output_path)],
                env=_probe_environment(),
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as timeout:
            raise RuntimeError(
                f"app_probe timed out after {_PROBE_TIMEOUT_SECONDS}s\nstderr:\n{_as_text(timeout.stderr)}"
            ) from timeout
        if result.returncode != 0:
            raise RuntimeError(f"app_probe failed with exit code {result.returncode}\nstderr:\n{result.stderr}")
        return json.loads(output_path.read_text(encoding="utf-8"))


@functools.cache
def _probe_outcome() -> dict[str, Any] | RuntimeError:
    # functools.cache stores returns only, so the failure is returned to be cached and raised by the caller.
    try:
        return _run_probe()
    except RuntimeError as failure:
        return failure


def load_guarded_app_dump() -> dict[str, Any]:
    """Return ``{"openapi", "routes", "contacts_attempted"}`` of the guarded app, built once per process.

    A failed probe is remembered too: every later call raises the same error without spawning again.
    """
    outcome = _probe_outcome()
    if isinstance(outcome, RuntimeError):
        raise outcome
    return outcome
