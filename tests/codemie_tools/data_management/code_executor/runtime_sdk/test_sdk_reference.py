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

"""Contract tests for the model-facing SDK reference that is shown in the script tool's description."""

from __future__ import annotations

import importlib.util
import json
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest

from codemie_tools.base.utils import get_encoding
from codemie_tools.data_management.code_executor.sdk_reference import REFERENCE_PATH, read_sdk_reference
from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings

SDK_PATH: Path = REFERENCE_PATH.parent / "codemie_runtime_sdk.py"
MAX_REFERENCE_TOKENS: int = 900

Handler = Callable[[str, Mapping[str, object]], object]


def _load_sdk() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codemie_runtime_sdk", SDK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _examples(text: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", text, re.S)


def _stub_backend(tool: str, args: Mapping[str, object]) -> object:
    """Plays the backend for the examples: a few known tools, everything else is refused like a real run would."""
    if tool == "generic_jira_tool" and str(args.get("relative_url", "")).startswith("/rest/api/2/issue/"):
        return {"result": {"fields": {"summary": "A summary"}}, "http": {"status": 200}}
    if tool == "generic_jira_tool":
        return {"result": {"issues": [{"key": "PROJ-1"}, {"key": "PROJ-2"}], "total": 2}, "http": {"status": 200}}
    if tool == "generic_confluence_tool":
        return {"result": {"results": [{"title": "Page"}]}, "http": {"status": 200}}
    raise _Refused("tool_unavailable", f"Tool '{tool}' is not available to this script run.")


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@contextmanager
def _exchange(tmp_path: Path, sdk: ModuleType, handler: Handler) -> Iterator[None]:
    """Serve the SDK's request files from a thread, the way the channel does on the real pod."""
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            for request_path in sorted(tmp_path.glob("req.*.json")):
                try:
                    request = json.loads(request_path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                request_path.unlink(missing_ok=True)
                payload = request["payload"]
                try:
                    result = handler(payload["name"], payload["args"])
                    body: dict[str, object] = {"v": request["v"], "id": request["id"], "ok": True, "result": result}
                except _Refused as refused:
                    error = {"code": refused.code, "message": refused.message}
                    body = {"v": request["v"], "id": request["id"], "ok": False, "error": error}
                (tmp_path / f"resp.{request['id']}.json").write_text(json.dumps(body), encoding="utf-8")
            stop.wait(0.01)

    sdk._configure({"exchange_dir": str(tmp_path), "run_seconds": 60.0})
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_sdk()
    monkeypatch.setitem(__import__("sys").modules, "codemie_runtime_sdk", module)
    return module


class TestReferenceContent:
    def test_reference_is_within_the_token_cap(self) -> None:
        tokens = len(get_encoding("gpt-4.1-mini").encode(read_sdk_reference()))

        assert tokens <= MAX_REFERENCE_TOKENS, tokens

    def test_documents_retryable_and_may_have_run_instead_of_a_code_table(self) -> None:
        text = read_sdk_reference()

        assert "error.retryable" in text
        assert "error.may_have_run" in text
        # The full per-code meanings live on the human-facing page, not here (every code is one bridge-wide
        # closed set tested elsewhere; repeating it in the model-facing text would only cost tokens).
        assert "no_context" not in text
        assert "deadline_exceeded" not in text

    def test_states_the_protocol_version_of_the_sdk(self, sdk: ModuleType) -> None:
        assert f"protocol version {sdk.PROTOCOL_VERSION}" in read_sdk_reference().lower()

    def test_examples_use_call_tool_or_call_tools_and_not_the_raw_call(self) -> None:
        examples = _examples(read_sdk_reference())

        assert examples
        assert all("call_tool(" in example or "call_tools(" in example for example in examples)
        assert not any("sdk.call(" in example or "import call\n" in example for example in examples)

    def test_teaches_call_tools_with_the_rule_about_calls_that_change_data(self) -> None:
        text = read_sdk_reference()

        assert "call_tools" in text
        assert "changes data" in text
        assert "needs another call's result" in text

    def test_no_placeholder_is_left_unfilled(self) -> None:
        assert "<<" not in read_sdk_reference()
        assert "<<" not in read_sdk_reference(ToolCallingSettings(max_parallel_calls=1))

    def test_the_numbers_are_the_ones_the_bridge_enforces(self, sdk: ModuleType) -> None:
        settings = ToolCallingSettings(max_payload_bytes=128 * 1024, max_parallel_calls=7)

        text = read_sdk_reference(settings)

        assert "128 KiB" in text
        assert "Up to 7 calls run at the same time" in text
        assert f"up to {sdk.MAX_BATCH_CALLS} calls" in text
        assert f"default {sdk.CALL_TIMEOUT_SECONDS:g} seconds" in text

    def test_does_not_list_excluded_tools_by_name(self) -> None:
        """A tool decides for itself whether a script may call it (script_callable); trying one outside the
        model's own tool list gets ``tool_unavailable`` from the backend, so the reference need not name any."""
        text = read_sdk_reference()

        for name in ("execute_workspace_script", "code_executor", "request_user_input"):
            assert name not in text

    def test_reference_is_not_part_of_the_injected_sdk_source(self) -> None:
        assert "```" not in SDK_PATH.read_text(encoding="utf-8")


class TestExamplesRunAgainstTheSdk:
    @pytest.mark.parametrize("index", range(2))
    def test_example_runs(
        self, index: int, sdk: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        examples = _examples(read_sdk_reference())
        assert len(examples) == 2, "the reference documents two short worked examples"

        with _exchange(tmp_path, sdk, _stub_backend):
            exec(compile(examples[index], f"reference-example-{index}", "exec"), {"__name__": "__main__"})  # noqa: S102

        assert capsys.readouterr().err == ""
