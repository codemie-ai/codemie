# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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


"""Static guard: every `ActivityEventCreate(...)` call site states `project_name` and `entity_label` (EPMCDME-14325).

A site that forgets the keyword silently records a NULL, which makes the event invisible in the project trail (AC1)
or unreadable once the entity is renamed or deleted (AC19). `project_name=None` is fine when written down: it is the
author saying "this event is global".
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[4] / "src" / "codemie"

# Global (not project-scoped) events: the keyword is intentionally absent.
NO_PROJECT_NAME = {
    "rest_api/routers/customer_config.py",  # marketplace threshold settings are tenant-wide
    "service/customer_config_service.py",  # customer config settings are tenant-wide
}


def _emit_sites() -> list[tuple[str, int, set[str | None]]]:
    sites = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == (
                "ActivityEventCreate"
            ):
                sites.append((path.relative_to(SRC).as_posix(), node.lineno, {kw.arg for kw in node.keywords}))
    return sites


SITES = _emit_sites()


def test_scan_finds_the_emit_sites():
    assert len(SITES) > 40


@pytest.mark.parametrize("file, line, keywords", SITES, ids=[f"{f}:{ln}" for f, ln, _ in SITES])
def test_site_states_project_name(file, line, keywords):
    assert None not in keywords, "ActivityEventCreate(**kwargs) hides which fields are stamped"
    if file not in NO_PROJECT_NAME:
        assert "project_name" in keywords, f"{file}:{line} must pass project_name= (None for a global event)"


@pytest.mark.parametrize("file, line, keywords", SITES, ids=[f"{f}:{ln}" for f, ln, _ in SITES])
def test_site_with_entity_type_states_entity_label(file, line, keywords):
    if "entity_type" in keywords:
        assert "entity_label" in keywords, f"{file}:{line} sets entity_type= but not entity_label="


def test_allowlists_have_no_stale_entries():
    files = {f for f, _, _ in SITES}
    assert files >= NO_PROJECT_NAME
