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

"""Tests for the stdlib-only runtime SDK used by scripts on the sandbox pod."""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

SDK_PATH: Path = (
    Path(__file__).resolve().parents[5]
    / "src"
    / "codemie_tools"
    / "data_management"
    / "code_executor"
    / "runtime_sdk"
    / "codemie_runtime_sdk.py"
)
MAX_SDK_BYTES: int = 12 * 1024
ALLOWED_IMPORTS: frozenset[str] = frozenset({"json", "os", "time", "uuid"})


def _load_sdk() -> ModuleType:
    """Load a fresh copy of the SDK by path, as the sandbox launcher does."""
    spec = importlib.util.spec_from_file_location("codemie_runtime_sdk_under_test", SDK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sdk() -> ModuleType:
    return _load_sdk()


def _imported_roots(tree: ast.AST) -> list[str]:
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import; record it as a distinct marker so it fails the allowlist
            roots.append("." * node.level + (node.module or "").split(".")[0])
    return roots


class TestSdkSourceInvariants:
    def test_imports_only_allowed_stdlib_modules(self) -> None:
        tree = ast.parse(SDK_PATH.read_text(encoding="utf-8"))

        roots = _imported_roots(tree)

        assert set(roots) <= ALLOWED_IMPORTS, roots
        assert not any(root.startswith("codemie") for root in roots)

    def test_source_is_within_size_cap(self) -> None:
        assert SDK_PATH.stat().st_size <= MAX_SDK_BYTES

    def test_clean_interpreter_import_adds_only_stdlib_modules(self) -> None:
        script = (
            "import importlib.util, sys\n"
            "before = set(sys.modules)\n"
            f"spec = importlib.util.spec_from_file_location('sdk_probe', {str(SDK_PATH)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "added = {name.split('.')[0] for name in set(sys.modules) - before}\n"
            "added.discard('sdk_probe')\n"
            "extra = sorted(name for name in added if name not in sys.stdlib_module_names)\n"
            "print(','.join(extra))\n"
            "print(any(name.startswith('codemie') for name in sys.modules))\n"
        )

        result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True)

        extra_line, codemie_line = result.stdout.splitlines()
        assert extra_line == ""
        assert codemie_line == "False"

    def test_import_writes_nothing_to_stdout(self) -> None:
        script = (
            "import importlib.util\n"
            f"spec = importlib.util.spec_from_file_location('sdk_probe', {str(SDK_PATH)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
        )

        result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True)

        assert result.stdout == ""

    def test_never_prints_or_touches_stdout(self) -> None:
        tree = ast.parse(SDK_PATH.read_text(encoding="utf-8"))

        printed = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
        ]
        stdout_uses = [
            node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in {"stdout", "__stdout__"}
        ]
        sys_imports = [root for root in _imported_roots(tree) if root == "sys"]

        assert printed == []
        assert stdout_uses == []
        assert sys_imports == []

    def test_top_level_holds_only_constants_and_definitions(self) -> None:
        tree = ast.parse(SDK_PATH.read_text(encoding="utf-8"))

        allowed = (
            ast.Import,
            ast.ImportFrom,
            ast.Assign,
            ast.AnnAssign,
            ast.FunctionDef,
            ast.ClassDef,
        )
        offenders = [
            node
            for node in tree.body
            if not isinstance(node, allowed)
            and not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]

        assert offenders == []

    def test_protocol_constants_are_exposed(self, sdk: ModuleType) -> None:
        assert sdk.BRIDGE_DIR_NAME == ".codemie_bridge"
        assert sdk.REQ_PREFIX == "req."
        assert sdk.RESP_PREFIX == "resp."
        assert sdk.MAX_PAYLOAD_BYTES == 256 * 1024
        assert sdk.CALL_TIMEOUT_SECONDS == 100.0
        assert isinstance(sdk.PROTOCOL_VERSION, int)


class TestUnconfiguredAndOversize:
    def test_unconfigured_call_raises_tool_call_error(self, sdk: ModuleType) -> None:
        with pytest.raises(sdk.ToolCallError, match="tool calling is not available in this run"):
            sdk.call("echo", {"x": 1})

    def test_oversize_request_rejected_before_writing(self, sdk: ModuleType, tmp_path: Path) -> None:
        sdk._configure(str(tmp_path))

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("echo", {"blob": "x" * (sdk.MAX_PAYLOAD_BYTES + 1)})

        assert exc_info.value.code == "payload_too_large"
        assert list(tmp_path.iterdir()) == []


def _serve_one(
    sdk: ModuleType, exchange: Path, build_response: Callable[[dict[str, object]], dict[str, object]]
) -> threading.Thread:
    """Stand-in backend: waits for a request file and writes the matching response file."""

    def _run() -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            requests = sorted(exchange.glob(f"{sdk.REQ_PREFIX}*.json"))
            if requests:
                request = json.loads(requests[0].read_text(encoding="utf-8"))
                response = build_response(request)
                target = exchange / f"{sdk.RESP_PREFIX}{request['id']}.json"
                tmp = exchange / f"{request['id']}.tmp"
                tmp.write_text(json.dumps(response), encoding="utf-8")
                tmp.replace(target)
                return
            time.sleep(0.01)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


@pytest.fixture
def configured(sdk: ModuleType, tmp_path: Path) -> Iterator[Path]:
    sdk._configure(str(tmp_path))
    yield tmp_path


class TestRoundTrip:
    def test_happy_path_returns_result_and_cleans_up(self, sdk: ModuleType, configured: Path) -> None:
        seen: list[dict[str, object]] = []

        def respond(request: dict[str, object]) -> dict[str, object]:
            seen.append(request)
            return {"v": request["v"], "id": request["id"], "ok": True, "result": {"echo": request["payload"]}}

        thread = _serve_one(sdk, configured, respond)

        result = sdk.call("echo", {"hello": "world"})
        thread.join(timeout=5)

        assert result == {"echo": {"hello": "world"}}
        assert seen[0]["op"] == "echo"
        assert seen[0]["payload"] == {"hello": "world"}
        assert seen[0]["v"] == sdk.PROTOCOL_VERSION
        assert isinstance(seen[0]["id"], str) and seen[0]["id"]
        assert list(configured.glob(f"{sdk.RESP_PREFIX}*.json")) == []

    def test_request_file_is_named_by_id(self, sdk: ModuleType, configured: Path) -> None:
        names: list[str] = []

        def respond(request: dict[str, object]) -> dict[str, object]:
            names.extend(p.name for p in configured.iterdir())
            return {"v": 1, "id": request["id"], "ok": True, "result": None}

        thread = _serve_one(sdk, configured, respond)

        sdk.call("echo", {})
        thread.join(timeout=5)

        assert any(n.startswith("req.") and n.endswith(".json") for n in names)

    def test_error_response_raises_with_code_and_message(self, sdk: ModuleType, configured: Path) -> None:
        def respond(request: dict[str, object]) -> dict[str, object]:
            return {
                "v": request["v"],
                "id": request["id"],
                "ok": False,
                "error": {"code": "unknown_op", "message": "no such op"},
            }

        thread = _serve_one(sdk, configured, respond)

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("nope", {})
        thread.join(timeout=5)

        assert exc_info.value.code == "unknown_op"
        assert "no such op" in str(exc_info.value)
        assert list(configured.glob(f"{sdk.RESP_PREFIX}*.json")) == []

    def test_timeout_raises_tool_call_error(self, sdk: ModuleType, configured: Path) -> None:
        started = time.monotonic()

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("echo", {}, timeout=0.3)

        assert exc_info.value.code == "timeout"
        assert time.monotonic() - started < 3.0

    def test_unavailable_marker_fails_fast_with_code_unavailable(self, sdk: ModuleType, configured: Path) -> None:
        (configured / sdk.UNAVAILABLE_MARKER_NAME).write_text("", encoding="utf-8")
        started = time.monotonic()

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("echo", {}, timeout=30.0)

        assert exc_info.value.code == "unavailable"
        assert time.monotonic() - started < 3.0
        assert list(configured.glob(f"{sdk.REQ_PREFIX}*.json")) == []
