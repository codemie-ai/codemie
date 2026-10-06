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

"""The scripts the backend sends to the sandbox pod are loaded as text and stay small and standard-library only."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from codemie_tools.data_management.code_executor import pod_scripts
from codemie_tools.data_management.code_executor.pod_scripts import load_pod_script

SCRIPT_NAMES: tuple[str, ...] = ("poll", "write_response")
ALLOWED_IMPORTS: frozenset[str] = frozenset({"contextlib", "json", "os", "shutil", "signal", "sys"})
MAX_SCRIPT_BYTES: int = 2048


def _scripts_dir() -> Path:
    return Path(pod_scripts.__file__).resolve().parent


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_script_source_is_valid_python(name: str) -> None:
    ast.parse(load_pod_script(name))


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_script_imports_only_the_standard_library_allowlist(name: str) -> None:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(load_pod_script(name))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= ALLOWED_IMPORTS


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_script_is_small_enough_to_travel_in_a_command_line(name: str) -> None:
    assert len(load_pod_script(name).encode("utf-8")) <= MAX_SCRIPT_BYTES


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_leading_comments_and_licence_header_are_not_sent(name: str) -> None:
    text = load_pod_script(name)
    assert text.startswith("import ")
    assert "Copyright" not in text
    assert "Licensed under" not in text


def test_every_script_file_is_covered_by_this_module() -> None:
    files = {path.stem for path in _scripts_dir().glob("*.py") if path.stem != "__init__"}
    assert files == set(SCRIPT_NAMES)


def test_unknown_script_is_an_error() -> None:
    with pytest.raises(FileNotFoundError):
        load_pod_script("does_not_exist")


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_importing_a_script_module_has_no_side_effects(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Tool discovery imports every module of the package, so a script must only run under `python3 -c`."""
    module = importlib.import_module(f"{pod_scripts.__name__}.{name}")
    assert callable(module.main)
    assert capsys.readouterr().out == ""
