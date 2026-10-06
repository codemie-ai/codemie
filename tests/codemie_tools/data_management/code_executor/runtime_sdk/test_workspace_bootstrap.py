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


def test_registers_sdk_module_and_skips_exchange_folder_when_the_config_has_no_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    codemie_bootstrap(_SDK_SOURCE, {})

    sdk = sys.modules["codemie_runtime_sdk"]
    assert sdk.BRIDGE_DIR_NAME == ".codemie_bridge"
    assert sdk._exchange_dir is None
    assert list(tmp_path.iterdir()) == []


def test_creates_the_exchange_folder_and_configures_the_sdk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    exchange_dir = ".codemie_bridge/123-abc"

    codemie_bootstrap(_SDK_SOURCE, {"exchange_dir": exchange_dir, "run_seconds": 180.0, "max_payload_bytes": 1000})

    run_dir = tmp_path / exchange_dir
    assert run_dir.is_dir()
    assert list(run_dir.iterdir()) == [], "the bootstrap writes nothing into the folder"
    sdk = sys.modules["codemie_runtime_sdk"]
    assert sdk._exchange_dir == exchange_dir
    assert sdk._run_seconds == 180.0
    assert sdk._max_payload_bytes == 1000


def test_reports_unavailable_on_stderr_when_folder_cannot_be_created(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".codemie_bridge").write_text("a file where the folder should be")

    codemie_bootstrap(_SDK_SOURCE, {"exchange_dir": ".codemie_bridge/123-abc"})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("codemie tool calling unavailable: ")
    assert captured.err.endswith("\n")
    assert sys.modules["codemie_runtime_sdk"]._exchange_dir is None


def test_a_missing_run_limit_stays_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    codemie_bootstrap(_SDK_SOURCE, {"exchange_dir": ".codemie_bridge/123-abc"})

    sdk = sys.modules["codemie_runtime_sdk"]
    assert sdk._run_seconds is None
    assert sdk._max_payload_bytes == sdk.MAX_PAYLOAD_BYTES
