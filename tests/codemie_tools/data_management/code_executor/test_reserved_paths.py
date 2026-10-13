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

"""The single definition of the workspace paths owned by the script runtime."""

from __future__ import annotations

import pytest

from codemie_tools.data_management.code_executor.reserved_paths import is_reserved_path


@pytest.mark.parametrize(
    "path",
    [
        ".codemie_bridge",
        ".codemie_bridge/x/req.1.json",
        "./.codemie_bridge/x",
        "sub/../.codemie_bridge",
        "/.codemie_bridge/x",
        ".codemie_bridge\\x",
        ".CODEMIE_BRIDGE/x",
        "\uff0ecodemie_bridge/x",  # fullwidth full stop folds to '.'
        "./a/../.codemie_bridge/x",
        "codemie_runtime_sdk.py",
        "./codemie_runtime_sdk.py",
        "Codemie_Runtime_SDK.py",
        "\uff43odemie_runtime_sdk.py",  # fullwidth 'c' folds to 'c'
        "a\uff0f..\uff0fcodemie_runtime_sdk.py",  # fullwidth solidus folds to '/', then '..' collapses
        ".codemie_bridge\uff3cx",  # fullwidth reverse solidus folds to a backslash separator
    ],
)
def test_reserved_spellings_are_detected(path: str) -> None:
    assert is_reserved_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "a.txt",
        "",
        ".codemie_bridge_other.txt",
        "sub/.codemie_bridge/x",
        "skills/foo/.codemie_bridge/x",
        "skills/foo/codemie_runtime_sdk.py",
        "codemie_runtime_sdk.py.bak",
        "codemie_runtime_sdk",
    ],
)
def test_other_paths_are_not_reserved(path: str) -> None:
    assert not is_reserved_path(path)


def test_there_is_one_definition_of_the_runtime_constants() -> None:
    import pathlib

    offenders = [
        str(path)
        for path in pathlib.Path("src").rglob("*.py")
        if ('BRIDGE_DIR_NAME = "' in path.read_text() or 'RUNTIME_SDK_FILE_NAME = "' in path.read_text())
        and path.name != "codemie_runtime_sdk.py"
    ]

    assert offenders == []
    assert not hasattr(
        __import__("codemie_tools.data_management.code_executor.job_bridge", fromlist=["x"]), "is_bridge_path"
    )


def test_sdk_file_name_does_not_depend_on_how_the_module_is_shipped(monkeypatch) -> None:
    import importlib

    from codemie_tools.data_management.code_executor import reserved_paths
    from codemie_tools.data_management.code_executor.runtime_sdk import codemie_runtime_sdk

    monkeypatch.setattr(codemie_runtime_sdk, "__file__", "/app/runtime_sdk/codemie_runtime_sdk.pyc")
    try:
        reloaded = importlib.reload(reserved_paths)
        assert reloaded.RUNTIME_SDK_FILE_NAME == "codemie_runtime_sdk.py"
        assert reloaded.is_reserved_path("codemie_runtime_sdk.py")
    finally:
        monkeypatch.undo()
        importlib.reload(reserved_paths)
