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

"""Disable unavailable MCP links inside a workflow YAML text, touching nothing else.

The edit is done on the text itself, located through `yaml.compose` node marks (no dump round trip, so comments,
quoting, ordering and line endings survive). The result is confirmed by re-parsing it: only the reported nodes
changed, and each of them is now disabled. If that cannot be shown, `WorkflowYamlEditError` is raised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.rest_api.models.mcp_restore import DisabledMcpServer
from codemie.service.mcp.access_control import MCPAccessControlService, NodeAddress

_MERGE_TAG = "tag:yaml.org,2002:merge"
_NODE_PROPERTIES = re.compile(r"^(?:[&!][^\s,\[\]{}]*\s+)*")
_LINE_BREAK = re.compile(r"\r\n|\n|\r")
_ADDRESS = re.compile(r"^(?:assistants\[(\d+)\]\.mcp_servers\[(\d+)\]|tools\[(\d+)\]\.mcp_server)$")


class WorkflowYamlEditError(Exception):
    """The YAML cannot be edited, or the edit cannot be proven to change only the reported nodes."""


@dataclass(frozen=True)
class _Entry:
    value: yaml.Node
    owner: yaml.MappingNode


@dataclass(frozen=True)
class _Edit:
    """Either replace the text of `end - start` chars (insert=None) or insert `text` at `start`."""

    start: int
    end: int
    text: str


def _effective_entries(mapping: yaml.MappingNode, _seen: frozenset[int] = frozenset()) -> dict[str, _Entry]:
    """The keys a mapping has after `<<` merges: direct keys win; of several merge sources the earlier wins."""
    if id(mapping) in _seen:
        return {}
    seen = _seen | {id(mapping)}
    result: dict[str, _Entry] = {}
    for key_node, value_node in mapping.value:
        if key_node.tag != _MERGE_TAG:
            continue
        sources = value_node.value if isinstance(value_node, yaml.SequenceNode) else [value_node]
        for source in reversed(sources):
            if isinstance(source, yaml.MappingNode):
                result.update(_effective_entries(source, seen))
    for key_node, value_node in mapping.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.tag != _MERGE_TAG:
            result[key_node.value] = _Entry(value_node, mapping)
    return result


def _child(node: yaml.Node | None, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    entry = _effective_entries(node).get(key)
    return entry.value if entry else None


def _locate(root: yaml.Node, address: str) -> yaml.MappingNode:
    match = _ADDRESS.match(address)
    node: yaml.Node | None = None
    if match:
        if match.group(3) is None:
            assistants = _child(root, "assistants")
            assistant = _seq_item(assistants, int(match.group(1)))
            node = _seq_item(_child(assistant, "mcp_servers"), int(match.group(2)))
        else:
            node = _child(_seq_item(_child(root, "tools"), int(match.group(3))), "mcp_server")
    if not isinstance(node, yaml.MappingNode):
        raise WorkflowYamlEditError(f"Cannot locate {address} in the workflow YAML")
    return node


def _seq_item(node: yaml.Node | None, index: int) -> yaml.Node | None:
    if isinstance(node, yaml.SequenceNode) and index < len(node.value):
        return node.value[index]
    return None


def _first_key_start(mapping: yaml.MappingNode) -> yaml.Mark:
    return mapping.value[0][0].start_mark


def _edit_for(text: str, mapping: yaml.MappingNode, entry: _Entry | None, eol: str) -> _Edit:
    """Set `enabled: false` on `mapping`: replace the value if `entry` is its own, else add the key to it."""
    if entry is not None and entry.owner is mapping:
        value = entry.value
        if not isinstance(value, yaml.ScalarNode):
            raise WorkflowYamlEditError("'enabled' is not a plain value")
        raw = text[value.start_mark.index : value.end_mark.index]
        props = _NODE_PROPERTIES.match(raw)
        return _Edit(value.start_mark.index + (props.end() if props else 0), value.end_mark.index, "false")
    if not mapping.value:
        raise WorkflowYamlEditError("Cannot add 'enabled' to an empty mapping")
    first = _first_key_start(mapping)
    if mapping.flow_style:
        return _Edit(first.index, first.index, "enabled: false, ")
    line = f"{' ' * first.column}enabled: false{eol}"
    end = _block_end(mapping, first.index, first.index)
    if end > 0 and text[end - 1] in "\r\n":  # block scalar: its mark already sits at the start of the next line
        return _Edit(end, end, line)
    line_end = _LINE_BREAK.search(text, end)
    if line_end is None:
        return _Edit(len(text), len(text), f"{eol}{line[: -len(eol)]}")
    return _Edit(line_end.end(), line_end.end(), line)


def _block_end(node: yaml.Node, fallback: int, lower: int) -> int:
    """Index where a block-style node's text ends. A node anchored before `lower` is an alias: use `fallback`."""
    if node.start_mark.index < lower:
        return fallback
    if isinstance(node, yaml.MappingNode) and node.value and not node.flow_style:
        key, value = node.value[-1]
        return _block_end(value, key.end_mark.index, lower)
    if isinstance(node, yaml.SequenceNode) and node.value and not node.flow_style:
        return _block_end(node.value[-1], fallback, lower)
    return node.end_mark.index


def _edit_target(
    mapping: yaml.MappingNode, entries: dict[str, _Entry], share_anchors: bool
) -> tuple[yaml.MappingNode, _Entry | None]:
    """The node that receives `enabled` and its existing entry there, if any."""
    entry = entries.get("enabled")
    if share_anchors:
        if entry is not None:
            return entry.owner, entry
        id_entry = entries.get("mcp_config_id")
        return (id_entry.owner if id_entry is not None else mapping), entry
    if entry is not None and entry.owner is not mapping:
        entry = None
    return mapping, entry


def _plan_edits(text: str, root: yaml.Node, addresses: list[str], eol: str, share_anchors: bool) -> list[_Edit]:
    """One edit per distinct place. With share_anchors the key is set where it is inherited from, else on the node."""
    edits: dict[tuple[int, str], _Edit] = {}
    for address in addresses:
        mapping = _locate(root, address)
        entries = _effective_entries(mapping)
        target, entry = _edit_target(mapping, entries, share_anchors)
        edits.setdefault(
            (id(target), "enabled"), _edit_for(text, target, entry if entry and entry.owner is target else None, eol)
        )
    return list(edits.values())


def _apply(text: str, edits: list[_Edit]) -> str:
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        text = text[: edit.start] + edit.text + text[edit.end :]
    return text


def _parse_nodes(text: str) -> list[tuple[NodeAddress, Any]]:
    scratch = WorkflowConfig(name="scratch", description="scratch", yaml_config=text)
    try:
        scratch.parse_execution_config()
    except Exception as e:  # noqa: BLE001 - any parse/validation failure means we cannot edit safely
        raise WorkflowYamlEditError(f"Workflow YAML cannot be parsed: {e}") from e
    return MCPAccessControlService.collect_workflow_mcp_nodes(scratch)


def _copy_without_mcp(value: Any, drop: frozenset[str] = frozenset({"mcp_servers", "mcp_server"})) -> Any:
    """A deep copy of the parsed YAML without MCP server entries (and un-shared), for comparing everything else."""
    if isinstance(value, dict):
        return {k: _copy_without_mcp(v) for k, v in value.items() if k not in drop}
    if isinstance(value, list):
        return [_copy_without_mcp(v) for v in value]
    return value


def _confirmed(old_text: str, new_text: str, addresses: list[str]) -> bool:
    """True when re-parsing shows only the reported nodes changed, and each of them is now disabled."""
    try:
        old_nodes, new_nodes = _parse_nodes(old_text), _parse_nodes(new_text)
        old_data, new_data = yaml.safe_load(old_text) or {}, yaml.safe_load(new_text) or {}
    except (WorkflowYamlEditError, yaml.YAMLError):
        return False
    if [a for a, _ in old_nodes] != [a for a, _ in new_nodes]:
        return False
    reported = set(addresses)
    for (address, before), (_, after) in zip(old_nodes, new_nodes, strict=True):
        if address.render() in reported:
            if after.enabled is not False:
                return False
        elif after.enabled != before.enabled:
            return False
        if before.model_dump(exclude={"enabled"}) != after.model_dump(exclude={"enabled"}):
            return False
    keys = set(old_data) | set(new_data)
    return all(_copy_without_mcp(old_data.get(k)) == _copy_without_mcp(new_data.get(k)) for k in keys)


def disable_dead_links_in_yaml(yaml_text: str, *, strict_lookup: bool = False) -> tuple[str, list[DisabledMcpServer]]:
    """Return (new_text, report): `yaml_text` with every unavailable MCP link set to `enabled: false`.

    Nothing is written anywhere and no live workflow is touched; the only I/O is the catalogue read of the restore
    helper. Text outside the edited values is byte-identical. Raises WorkflowYamlEditError when the YAML cannot be
    parsed or the edit cannot be confirmed by re-parsing; CatalogLookupError from the catalogue read.
    """
    nodes = _parse_nodes(yaml_text)
    _, report = MCPAccessControlService.restore_mcp_servers(
        [server for _, server in nodes],
        addresses=[address.render() for address, _ in nodes],
        strict_lookup=strict_lookup,
    )
    new_text = yaml_text
    if report:
        new_text = _disable_reported(yaml_text, [item.address for item in report if item.address])
    return new_text, report


def _disable_reported(yaml_text: str, addresses: list[str]) -> str:
    """`yaml_text` with `enabled: false` set on each addressed node, confirmed by re-parsing."""
    try:
        root = yaml.compose(yaml_text)
    except yaml.YAMLError as e:
        raise WorkflowYamlEditError(f"Workflow YAML cannot be parsed: {e}") from e
    if root is None:
        raise WorkflowYamlEditError("Workflow YAML is empty")

    first_break = _LINE_BREAK.search(yaml_text)
    eol = first_break.group(0) if first_break else "\n"
    # Plan A edits a shared anchor once when the node inherits `enabled` from it; plan B sets the key on each
    # reported node itself (an explicit key beats a merge), leaving anchors alone.
    for share_anchors in (True, False):
        new_text = _apply(yaml_text, _plan_edits(yaml_text, root, addresses, eol, share_anchors))
        if _confirmed(yaml_text, new_text, addresses):
            return new_text
    raise WorkflowYamlEditError(
        "Cannot disable the unavailable MCP servers without changing other workflow nodes: " + ", ".join(addresses)
    )
