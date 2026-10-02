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

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from codemie_tools.data_management.code_executor.runtime_sdk import codemie_runtime_sdk
from codemie_tools.data_management.code_executor.runtime_sdk.workspace_bootstrap import codemie_bootstrap

_SDK_SOURCE = Path(codemie_runtime_sdk.__file__).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _restore_sdk_module() -> Iterator[None]:
    previous = sys.modules.get("codemie_runtime_sdk")
    yield
    if previous is None:
        sys.modules.pop("codemie_runtime_sdk", None)
    else:
        sys.modules["codemie_runtime_sdk"] = previous


def test_registers_sdk_module_and_skips_exchange_folder_when_dir_is_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    codemie_bootstrap(_SDK_SOURCE, None)

    sdk = sys.modules["codemie_runtime_sdk"]
    assert sdk.BRIDGE_DIR_NAME == ".codemie_bridge"
    assert list(tmp_path.iterdir()) == []


def test_creates_exchange_folder_with_pid_and_configures_sdk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    real_read_text = Path.read_text
    fake_stat = "1 (python) S " + " ".join(str(n) for n in range(2, 40))

    def read_text(self: Path, *args: object, **kwargs: object) -> str:
        if str(self) == "/proc/self/stat":
            return fake_stat
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    exchange_dir = ".codemie_bridge/123-abc"

    codemie_bootstrap(_SDK_SOURCE, exchange_dir)

    run_dir = tmp_path / exchange_dir
    assert (run_dir / "pid").read_text() != ""
    assert (run_dir / "start_time").read_text() == "20"
    assert sys.modules["codemie_runtime_sdk"]._state == exchange_dir


def test_reports_unavailable_on_stderr_when_folder_cannot_be_created(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".codemie_bridge").write_text("a file where the folder should be")

    codemie_bootstrap(_SDK_SOURCE, ".codemie_bridge/123-abc")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("codemie tool calling unavailable: ")
    assert captured.err.endswith("\n")
