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
MAX_SDK_BYTES: int = 16 * 1024
ALLOWED_IMPORTS: frozenset[str] = frozenset({"json", "os", "time", "uuid"})


def _config(exchange: Path, run_seconds: float | None = None, **extra: object) -> dict[str, object]:
    """The SDK configuration the bootstrap hands over."""
    config: dict[str, object] = {"exchange_dir": str(exchange), **extra}
    if run_seconds is not None:
        config["run_seconds"] = run_seconds
    return config


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
        sdk._configure(_config(tmp_path))

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
    sdk._configure(_config(tmp_path))
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


_EXPECTED_ERROR_CODES: frozenset[str] = frozenset(
    {
        "no_context",
        "tool_blocked",
        "tool_unavailable",
        "bad_arguments",
        "tool_failed",
        "unknown_op",
        "bad_request",
        "payload_too_large",
        "internal_error",
        "unavailable",
        "timeout",
        "error",
        "deadline_exceeded",
    }
)


class TestErrorCodes:
    def test_error_codes_are_the_closed_documented_set(self, sdk: ModuleType) -> None:
        assert sdk.ERROR_CODES == _EXPECTED_ERROR_CODES
        assert isinstance(sdk.ERROR_CODES, frozenset)

    def test_error_codes_are_built_from_the_named_constants(self, sdk: ModuleType) -> None:
        constants = {value for name, value in vars(sdk).items() if name.startswith("CODE_")}

        assert constants == sdk.ERROR_CODES

    def test_default_error_code_is_in_the_set(self, sdk: ModuleType) -> None:
        assert sdk.ToolCallError("boom").code in sdk.ERROR_CODES


class TestCallTool:
    def test_sends_the_tool_call_operation_and_returns_the_envelope(self, sdk: ModuleType, configured: Path) -> None:
        seen: list[dict[str, object]] = []

        def respond(request: dict[str, object]) -> dict[str, object]:
            seen.append(request)
            envelope = {"result": {"key": "X-1"}, "http": {"status": 200, "reason": "OK"}}
            return {"v": request["v"], "id": request["id"], "ok": True, "result": envelope}

        thread = _serve_one(sdk, configured, respond)

        result = sdk.call_tool("jira_search", {"jql": "project = X"})
        thread.join(timeout=5)

        assert result == {"result": {"key": "X-1"}, "http": {"status": 200, "reason": "OK"}}
        assert seen[0]["op"] == "tool.call"
        assert seen[0]["payload"] == {"name": "jira_search", "args": {"jql": "project = X"}}

    def test_args_default_to_an_empty_object(self, sdk: ModuleType, configured: Path) -> None:
        seen: list[dict[str, object]] = []

        def respond(request: dict[str, object]) -> dict[str, object]:
            seen.append(request)
            return {"v": request["v"], "id": request["id"], "ok": True, "result": {"result": None}}

        thread = _serve_one(sdk, configured, respond)

        sdk.call_tool("list_workspace_files")
        thread.join(timeout=5)

        assert seen[0]["payload"] == {"name": "list_workspace_files", "args": {}}

    def test_timeout_is_forwarded_to_call(self, sdk: ModuleType, configured: Path) -> None:
        started = time.monotonic()

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call_tool("slow", {}, timeout=0.3)

        assert exc_info.value.code == "timeout"
        assert time.monotonic() - started < 3.0

    def test_error_response_raises_with_the_backend_code(self, sdk: ModuleType, configured: Path) -> None:
        def respond(request: dict[str, object]) -> dict[str, object]:
            error = {"code": "tool_unavailable", "message": "not available"}
            return {"v": request["v"], "id": request["id"], "ok": False, "error": error}

        thread = _serve_one(sdk, configured, respond)

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call_tool("nope")
        thread.join(timeout=5)

        assert exc_info.value.code == "tool_unavailable"


class TestRunLimitAwareWait:
    def test_wait_is_capped_by_the_time_left_of_the_run_limit(self, sdk: ModuleType, tmp_path: Path) -> None:
        sdk._configure(_config(tmp_path, 0.4))
        started = time.monotonic()

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("echo", {}, timeout=60.0)

        assert exc_info.value.code == "timeout"
        assert time.monotonic() - started < 3.0
        assert list(tmp_path.glob(f"{sdk.REQ_PREFIX}*.json")) == []

    def test_own_timeout_wins_when_shorter_than_the_time_left(self, sdk: ModuleType, tmp_path: Path) -> None:
        sdk._configure(_config(tmp_path, 600.0))
        started = time.monotonic()

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("echo", {}, timeout=0.3)

        assert exc_info.value.code == "timeout"
        assert time.monotonic() - started < 3.0

    def test_exhausted_limit_raises_timeout_before_anything_is_sent(self, sdk: ModuleType, tmp_path: Path) -> None:
        sdk._configure(_config(tmp_path, 0.05))
        time.sleep(0.15)

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("echo", {})

        assert exc_info.value.code == "timeout"
        assert "not sent" in str(exc_info.value)
        assert list(tmp_path.iterdir()) == []

    def test_without_a_run_limit_the_default_wait_applies(self, sdk: ModuleType, tmp_path: Path) -> None:
        sdk._configure(_config(tmp_path))

        assert sdk._run_seconds is None
        assert sdk._max_payload_bytes == sdk.MAX_PAYLOAD_BYTES

    def test_configure_records_the_config_and_a_start(self, sdk: ModuleType, tmp_path: Path) -> None:
        before = time.monotonic()

        sdk._configure(_config(tmp_path, 180.0, max_payload_bytes=1000))

        assert sdk._exchange_dir == str(tmp_path)
        assert sdk._run_seconds == 180.0
        assert sdk._max_payload_bytes == 1000
        assert before <= sdk._started <= time.monotonic()

    def test_the_configured_payload_cap_is_the_one_enforced(self, sdk: ModuleType, tmp_path: Path) -> None:
        sdk._configure(_config(tmp_path, max_payload_bytes=200))

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("echo", {"blob": "x" * 300})

        assert exc_info.value.code == "payload_too_large"
        assert "200" in str(exc_info.value)


class TestFailureWording:
    def test_timeout_says_the_call_may_still_have_completed(self, sdk: ModuleType, configured: Path) -> None:
        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("echo", {}, timeout=0.2)

        message = str(exc_info.value)
        assert "may_have_run" in message
        assert "retry" in message
        assert exc_info.value.may_have_run is True

    def test_backend_stopped_answering_says_the_call_may_already_have_run(
        self, sdk: ModuleType, configured: Path
    ) -> None:
        (configured / sdk.UNAVAILABLE_MARKER_NAME).write_text("", encoding="utf-8")

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("echo", {}, timeout=30.0)

        message = str(exc_info.value)
        assert exc_info.value.code == "unavailable"
        assert "may_have_run" in message
        assert "retry" in message
        assert exc_info.value.may_have_run is True

    def test_unconfigured_unavailable_keeps_its_meaning(self, sdk: ModuleType) -> None:
        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call("echo", {})

        assert exc_info.value.code == "unavailable"
        assert exc_info.value.may_have_run is False


def _serve_many(
    sdk: ModuleType,
    exchange: Path,
    expected: int,
    respond: Callable[[list[dict[str, object]]], dict[str, dict[str, object]]],
    *,
    seen: list[int] | None = None,
) -> threading.Thread:
    """Stand-in backend for a batch: waits until ``expected`` request files exist, then answers them all.

    ``respond`` gets the requests and returns the response for each request id (an id left out stays unanswered).
    """

    def _run() -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            files = sorted(exchange.glob(f"{sdk.REQ_PREFIX}*.json"))
            if len(files) >= expected:
                if seen is not None:
                    seen.append(len(files))
                requests = [json.loads(path.read_text(encoding="utf-8")) for path in files]
                for request_id, response in respond(requests).items():
                    tmp = exchange / f"{request_id}.tmp"
                    tmp.write_text(json.dumps(response), encoding="utf-8")
                    tmp.replace(exchange / f"{sdk.RESP_PREFIX}{request_id}.json")
                    (exchange / f"{sdk.REQ_PREFIX}{request_id}.json").unlink(missing_ok=True)  # as the backend does
                return
            time.sleep(0.01)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _ok(request: dict[str, object], result: object) -> dict[str, object]:
    return {"v": request["v"], "id": request["id"], "ok": True, "result": result}


def _failed(request: dict[str, object], code: str) -> dict[str, object]:
    return {"v": request["v"], "id": request["id"], "ok": False, "error": {"code": code, "message": f"{code}!"}}


class TestCallTools:
    def test_returns_the_envelopes_in_the_order_given_whatever_the_order_of_the_answers(
        self, sdk: ModuleType, configured: Path
    ) -> None:
        def respond(requests: list[dict[str, object]]) -> dict[str, dict[str, object]]:
            answers = {str(r["id"]): _ok(r, {"result": r["payload"]["name"]}) for r in requests}  # type: ignore[index]
            return dict(reversed(list(answers.items())))

        thread = _serve_many(sdk, configured, 3, respond)

        results = sdk.call_tools([{"name": "a"}, {"name": "b", "args": {"x": 1}}, {"name": "c"}])
        thread.join(timeout=5)

        assert results == [{"result": "a"}, {"result": "b"}, {"result": "c"}]
        assert list(configured.glob(f"{sdk.REQ_PREFIX}*.json")) == []
        assert list(configured.glob(f"{sdk.RESP_PREFIX}*.json")) == []

    def test_every_request_is_written_before_the_first_answer_is_waited_for(
        self, sdk: ModuleType, configured: Path
    ) -> None:
        seen: list[int] = []

        def respond(requests: list[dict[str, object]]) -> dict[str, dict[str, object]]:
            return {str(r["id"]): _ok(r, {"result": 1}) for r in requests}

        thread = _serve_many(sdk, configured, 4, respond, seen=seen)

        sdk.call_tools([{"name": "t"}] * 4)
        thread.join(timeout=5)

        assert seen == [4], "the backend must have seen all four requests at once"

    def test_a_failed_call_is_returned_as_an_error_item_and_the_others_are_results(
        self, sdk: ModuleType, configured: Path
    ) -> None:
        def respond(requests: list[dict[str, object]]) -> dict[str, dict[str, object]]:
            return {
                str(r["id"]): (
                    _failed(r, "tool_unavailable") if r["payload"]["name"] == "bad" else _ok(r, {"result": "fine"})  # type: ignore[index]
                )
                for r in requests
            }

        thread = _serve_many(sdk, configured, 3, respond)

        results = sdk.call_tools([{"name": "ok1"}, {"name": "bad"}, {"name": "ok2"}])
        thread.join(timeout=5)

        assert results[0] == {"result": "fine"}
        assert results[2] == {"result": "fine"}
        assert isinstance(results[1], sdk.ToolCallError)
        assert results[1].code == "tool_unavailable"

    def test_the_payload_of_each_request_is_the_tool_call_payload(self, sdk: ModuleType, configured: Path) -> None:
        payloads: list[object] = []

        def respond(requests: list[dict[str, object]]) -> dict[str, dict[str, object]]:
            payloads.extend((r["op"], r["payload"]) for r in requests)
            return {str(r["id"]): _ok(r, {"result": None}) for r in requests}

        thread = _serve_many(sdk, configured, 2, respond)

        sdk.call_tools([{"name": "t1", "args": {"q": "x"}}, {"name": "t2"}])
        thread.join(timeout=5)

        assert ("tool.call", {"name": "t1", "args": {"q": "x"}}) in payloads
        assert ("tool.call", {"name": "t2", "args": {}}) in payloads

    def test_any_other_key_of_an_item_goes_to_the_backend_with_the_call(
        self, sdk: ModuleType, configured: Path
    ) -> None:
        payloads: list[object] = []

        def respond(requests: list[dict[str, object]]) -> dict[str, dict[str, object]]:
            payloads.extend(r["payload"] for r in requests)
            return {str(r["id"]): _ok(r, {"result": None}) for r in requests}

        thread = _serve_many(sdk, configured, 1, respond)

        sdk.call_tools([{"name": "t", "args": {"q": 1}, "integration_alias": "work"}])
        thread.join(timeout=5)

        assert payloads == [{"name": "t", "args": {"q": 1}, "integration_alias": "work"}]

    def test_an_empty_batch_returns_an_empty_list_without_touching_the_exchange(
        self, sdk: ModuleType, configured: Path
    ) -> None:
        assert sdk.call_tools([]) == []
        assert list(configured.iterdir()) == []

    def test_a_batch_over_the_cap_is_refused_before_anything_is_sent(self, sdk: ModuleType, configured: Path) -> None:
        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call_tools([{"name": "t"}] * (sdk.MAX_BATCH_CALLS + 1))

        assert exc_info.value.code == "bad_arguments"
        assert list(configured.iterdir()) == []

    def test_a_batch_at_the_cap_is_accepted(self, sdk: ModuleType, configured: Path) -> None:
        def respond(requests: list[dict[str, object]]) -> dict[str, dict[str, object]]:
            return {str(r["id"]): _ok(r, {"result": 1}) for r in requests}

        thread = _serve_many(sdk, configured, sdk.MAX_BATCH_CALLS, respond)

        results = sdk.call_tools([{"name": "t"}] * sdk.MAX_BATCH_CALLS)
        thread.join(timeout=5)

        assert len(results) == sdk.MAX_BATCH_CALLS

    @pytest.mark.parametrize("calls", ["not a list", [("t", None)], [{"name": "t"}, 5], [{"args": {}}], [{"name": 1}]])
    def test_a_malformed_batch_is_refused_before_anything_is_sent(
        self, sdk: ModuleType, configured: Path, calls: object
    ) -> None:
        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call_tools(calls)  # type: ignore[arg-type]

        assert exc_info.value.code == "bad_arguments"
        assert list(configured.iterdir()) == []

    def test_an_oversize_request_is_an_error_item_and_the_rest_are_still_sent(
        self, sdk: ModuleType, tmp_path: Path
    ) -> None:
        sdk._configure(_config(tmp_path, max_payload_bytes=300))

        def respond(requests: list[dict[str, object]]) -> dict[str, dict[str, object]]:
            return {str(r["id"]): _ok(r, {"result": "sent"}) for r in requests}

        thread = _serve_many(sdk, tmp_path, 1, respond)

        results = sdk.call_tools([{"name": "big", "args": {"blob": "x" * 500}}, {"name": "small"}])
        thread.join(timeout=5)

        assert isinstance(results[0], sdk.ToolCallError)
        assert results[0].code == "payload_too_large"
        assert results[1] == {"result": "sent"}

    def test_unanswered_calls_time_out_as_items_and_the_answered_ones_are_kept(
        self, sdk: ModuleType, configured: Path
    ) -> None:
        def respond(requests: list[dict[str, object]]) -> dict[str, dict[str, object]]:
            first = next(r for r in requests if r["payload"]["name"] == "answered")  # type: ignore[index]
            return {str(first["id"]): _ok(first, {"result": "yes"})}

        thread = _serve_many(sdk, configured, 2, respond)
        started = time.monotonic()

        results = sdk.call_tools([{"name": "answered"}, {"name": "silent"}], timeout=0.4)
        thread.join(timeout=5)

        assert results[0] == {"result": "yes"}
        assert isinstance(results[1], sdk.ToolCallError)
        assert results[1].code == "timeout"
        assert "may_have_run" in str(results[1])
        assert results[1].may_have_run is True
        assert time.monotonic() - started < 3.0
        assert list(configured.glob(f"{sdk.REQ_PREFIX}*.json")) == [], "a given-up request file is removed"

    def test_the_batch_wait_never_goes_beyond_the_time_left_of_the_run_limit(
        self, sdk: ModuleType, tmp_path: Path
    ) -> None:
        sdk._configure(_config(tmp_path, 0.4))
        started = time.monotonic()

        results = sdk.call_tools([{"name": "a"}, {"name": "b"}], timeout=60.0)

        assert [r.code for r in results] == ["timeout", "timeout"]
        assert time.monotonic() - started < 3.0

    def test_a_used_up_run_limit_raises_before_anything_is_sent(self, sdk: ModuleType, tmp_path: Path) -> None:
        sdk._configure(_config(tmp_path, 0.05))
        time.sleep(0.15)

        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call_tools([{"name": "a"}])

        assert exc_info.value.code == "timeout"
        assert list(tmp_path.iterdir()) == []

    def test_unavailable_marker_fails_the_unanswered_calls_fast(self, sdk: ModuleType, configured: Path) -> None:
        (configured / sdk.UNAVAILABLE_MARKER_NAME).write_text("", encoding="utf-8")
        started = time.monotonic()

        results = sdk.call_tools([{"name": "a"}, {"name": "b"}], timeout=30.0)

        assert [r.code for r in results] == ["unavailable", "unavailable"]
        assert time.monotonic() - started < 3.0
        assert list(configured.glob(f"{sdk.REQ_PREFIX}*.json")) == []

    def test_unconfigured_raises_unavailable_for_the_whole_batch(self, sdk: ModuleType) -> None:
        with pytest.raises(sdk.ToolCallError) as exc_info:
            sdk.call_tools([{"name": "a"}])

        assert exc_info.value.code == "unavailable"
