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

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from codemie.service.mcp.access_control import CatalogLookupError
from codemie.service.mcp.workflow_yaml_editor import WorkflowYamlEditError, disable_dead_links_in_yaml

_GET_BY_IDS = "codemie.service.mcp.access_control.MCPConfig.get_by_ids"


def _entry(entry_id: str, active: bool = True, public: bool = True):
    return SimpleNamespace(id=entry_id, is_active=active, is_public=public)


def _run(text: str, *entries, **kwargs):
    with patch(_GET_BY_IDS, side_effect=lambda ids: [e for e in entries if e.id in ids]):
        return disable_dead_links_in_yaml(text, **kwargs)


def _servers(text: str) -> list[dict]:
    data = yaml.safe_load(text)
    out = []
    for a in data.get("assistants", []):
        out.extend(a.get("mcp_servers", []))
    out.extend(t["mcp_server"] for t in data.get("tools", []) if t.get("mcp_server"))
    return out


BLOCK = """\
# header comment
assistants:
  - id: a1
    model: gpt
    system_prompt: hi
    mcp_servers:
      - name: dead  # keep me
        mcp_config_id: dead-1
        enabled: true
      - name: live
        mcp_config_id: live-1
        enabled: true
      - name: noflag
        mcp_config_id: dead-1
tools:
  - id: t1
    tool: x
    mcp_server:
      name: tdead
      mcp_config_id: dead-1
states: []
"""


def test_block_flip_and_insert_leave_everything_else_byte_identical():
    new, report = _run(BLOCK, _entry("live-1"))

    assert [r.address for r in report] == [
        "assistants[0].mcp_servers[0]",
        "assistants[0].mcp_servers[2]",
        "tools[0].mcp_server",
    ]
    assert all(r.reason == "missing" for r in report)
    assert [s["enabled"] for s in _servers(new)] == [False, True, False, False]
    # undoing the three edits gives back the original text exactly
    undone = new.replace("enabled: false", "enabled: true", 1)
    undone = undone.replace("        enabled: false\n", "").replace("      enabled: false\n", "")
    assert undone == BLOCK


def test_no_dead_links_returns_text_unchanged():
    new, report = _run(BLOCK, _entry("live-1"), _entry("dead-1"))

    assert new == BLOCK
    assert report == []


def test_flow_mapping_inline_insert_and_flip():
    text = (
        "assistants:\n"
        "  - id: a1\n"
        "    mcp_servers:\n"
        "      - {name: d, mcp_config_id: dead-1}\n"
        "      - {name: e, mcp_config_id: dead-1, enabled: true}\n"
    )
    new, report = _run(text)

    assert len(report) == 2
    assert [s["enabled"] for s in _servers(new)] == [False, False]
    assert new.count("\n") == text.count("\n")


def test_crlf_preserved():
    text = BLOCK.replace("\n", "\r\n")
    new, _ = _run(text, _entry("live-1"))

    assert "\n" not in new.replace("\r\n", "")
    assert [s["enabled"] for s in _servers(new)] == [False, True, False, False]


def test_document_start_marker_kept():
    new, report = _run("---\n" + BLOCK, _entry("live-1"))

    assert new.startswith("---\n# header")
    assert len(report) == 3


def test_same_dead_id_in_two_nodes_both_disabled():
    text = (
        "assistants:\n"
        "  - id: a1\n    mcp_servers:\n      - {name: x, mcp_config_id: dead-1, enabled: true}\n"
        "  - id: a2\n    mcp_servers:\n      - name: y\n        mcp_config_id: dead-1\n        enabled: true\n"
    )
    new, report = _run(text)

    assert {r.address for r in report} == {"assistants[0].mcp_servers[0]", "assistants[1].mcp_servers[0]"}
    assert [s["enabled"] for s in _servers(new)] == [False, False]


def test_custom_config_entry_with_dead_id_untouched():
    text = (
        "assistants:\n"
        "  - id: a1\n    mcp_servers:\n"
        "      - name: custom\n        mcp_config_id: dead-1\n        use_custom_config: true\n"
        "        config: {command: npx}\n        enabled: true\n"
        "      - name: link\n        mcp_config_id: dead-1\n        enabled: true\n"
    )
    new, report = _run(text)

    assert [r.name for r in report] == ["link"]
    assert [s["enabled"] for s in _servers(new)] == [True, False]


def test_alias_node_with_own_enabled_edits_the_node_not_the_anchor():
    text = (
        "defs:\n"
        "  base: &mcp\n    mcp_config_id: dead-1\n    enabled: true\n"
        "assistants:\n"
        "  - id: a1\n    mcp_servers:\n"
        "      - name: x\n        <<: *mcp\n        enabled: true\n"
    )
    new, report = _run(text)

    assert [s["enabled"] for s in _servers(new)] == [False]
    assert yaml.safe_load(new)["defs"]["base"]["enabled"] is True
    assert len(report) == 1


def test_shared_anchor_under_unlisted_key_is_overridden_per_node_and_reports_all():
    text = (
        "defs:\n"
        "  base: &mcp\n    mcp_config_id: dead-1\n    enabled: true\n"
        "assistants:\n"
        "  - id: a1\n    mcp_servers:\n"
        "      - name: x\n        <<: *mcp\n"
        "      - name: y\n        <<: *mcp\n"
    )
    new, report = _run(text)

    assert {r.address for r in report} == {"assistants[0].mcp_servers[0]", "assistants[0].mcp_servers[1]"}
    assert [s["enabled"] for s in _servers(new)] == [False, False]
    # An anchor under an unlisted top-level key cannot be shown unchanged, so each node gets its own override.
    assert yaml.safe_load(new)["defs"]["base"]["enabled"] is True


def test_shared_anchor_that_would_flip_unreported_node_uses_override():
    text = (
        "defs:\n"
        "  base: &mcp\n    mcp_config_id: dead-1\n    enabled: true\n"
        "assistants:\n"
        "  - id: a1\n    mcp_servers:\n"
        "      - name: x\n        <<: *mcp\n"
        "      - name: y\n        <<: *mcp\n        mcp_config_id: live-1\n"
    )
    new, report = _run(text, _entry("live-1"))

    assert [r.address for r in report] == ["assistants[0].mcp_servers[0]"]
    assert [s["enabled"] for s in _servers(new)] == [False, True]
    assert yaml.safe_load(new)["defs"]["base"]["enabled"] is True


def test_item_alias_shared_between_dead_nodes_disables_both():
    text = (
        "assistants:\n"
        "  - id: a1\n    mcp_servers:\n"
        "      - &s {name: x, mcp_config_id: dead-1, enabled: true}\n"
        "  - id: a2\n    mcp_servers:\n"
        "      - *s\n"
    )
    new, report = _run(text)

    assert len(report) == 2
    assert [s["enabled"] for s in _servers(new)] == [False, False]


def test_unprovable_edit_raises():
    text = (
        "assistants:\n"
        "  - id: a1\n    mcp_servers:\n"
        "      - &s {name: x, mcp_config_id: dead-1, enabled: true}\n"
        "tools:\n"
        "  - id: t2\n    tool: z\n    mcp_server: {name: w, mcp_config_id: live-1, <<: *s}\n"
    )
    with pytest.raises(WorkflowYamlEditError):
        _run(text, _entry("live-1"))


def test_invalid_yaml_raises_edit_error():
    with pytest.raises(WorkflowYamlEditError):
        _run("assistants: [\n")


def test_strict_lookup_error_propagates():
    with patch(_GET_BY_IDS, return_value=[]), pytest.raises(CatalogLookupError):
        disable_dead_links_in_yaml(BLOCK, strict_lookup=True)


def test_confirmation_compares_unlisted_top_level_keys():
    from codemie.service.mcp.workflow_yaml_editor import _confirmed

    old = "assistants: []\nsome_future_key: 1\n"
    assert _confirmed(old, old, []) is True
    assert _confirmed(old, "assistants: []\nsome_future_key: 2\n", []) is False
    assert _confirmed(old, "assistants: []\nsome_future_key: 1\nextra: 3\n", []) is False


def test_edit_hook_has_a_single_return_point():
    import ast
    import inspect
    import textwrap

    from codemie.service.mcp import workflow_yaml_editor

    tree = ast.parse(textwrap.dedent(inspect.getsource(workflow_yaml_editor.disable_dead_links_in_yaml)))
    returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
    assert len(returns) == 1
