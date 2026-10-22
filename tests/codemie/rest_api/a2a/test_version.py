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

"""Unit tests for the A2A protocol version placeholder constant."""

import ast
from pathlib import Path

from codemie.rest_api.a2a.version import A2A_PROTOCOL_VERSION


def test_a2a_protocol_version_is_confirmed_negotiation_value():
    assert A2A_PROTOCOL_VERSION == "1.0"


def test_version_module_has_no_third_party_imports():
    repo_root = Path(__file__).resolve().parents[4]
    source = (repo_root / "src" / "codemie" / "rest_api" / "a2a" / "version.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not isinstance(
            node, (ast.Import, ast.ImportFrom)
        ), "version.py must not import anything — it is a leaf constants module"
