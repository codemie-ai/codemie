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

"""The CLI Analytics repository layer never reaches up into the service layer."""

from __future__ import annotations

import ast
from pathlib import Path

import codemie.repository.cli_analytics as cli_analytics

_SERVICE = "codemie.service"


def _imports_service(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(a.name == _SERVICE or a.name.startswith(_SERVICE + ".") for a in node.names)
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return node.module == _SERVICE or node.module.startswith(_SERVICE + ".")
    return False


def test_repository_cli_analytics_imports_nothing_from_service() -> None:
    # The delivery-framework classifier is handed to the refresher from outside.
    root = Path(cli_analytics.__file__).parent
    modules = sorted(root.rglob("*.py"))
    offenders = [
        f"{path.relative_to(root)}:{node.lineno}"
        for path in modules
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if _imports_service(node)
    ]

    assert modules
    assert offenders == []
