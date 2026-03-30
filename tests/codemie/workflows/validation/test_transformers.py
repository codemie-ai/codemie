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

"""Unit tests for PydanticErrorTransformer in codemie.workflows.validation.transformers."""

from unittest.mock import MagicMock, patch
from pydantic import BaseModel, ValidationError as PydanticValidationError

from codemie.workflows.validation.transformers import PydanticErrorTransformer
from codemie.workflows.validation.models import MCPMeta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DummyModel(BaseModel):
    value: int


def _make_pydantic_error() -> PydanticValidationError:
    try:
        _DummyModel(value="not_an_int")
    except PydanticValidationError as exc:
        return exc
    raise AssertionError("Expected PydanticValidationError was not raised")


def _make_transformer(yaml_config: str = "{}") -> PydanticErrorTransformer:
    """Create a PydanticErrorTransformer with a mocked workflow_config."""
    mock_workflow_config = MagicMock()
    mock_workflow_config.yaml_config = yaml_config
    mock_validation_error = MagicMock()
    mock_validation_error.errors.return_value = []
    return PydanticErrorTransformer(mock_validation_error, mock_workflow_config)


def _make_transformer_with_data(yaml_data: dict) -> PydanticErrorTransformer:
    """Create a transformer with _yaml_data already set (bypasses YAML parsing)."""
    transformer = _make_transformer()
    transformer._yaml_data = yaml_data
    transformer._line_finder = MagicMock()
    return transformer


# ---------------------------------------------------------------------------
# 1. Static method: _build_field_path_with_brackets
# ---------------------------------------------------------------------------


def test_build_field_path_with_brackets_empty_list():
    result = PydanticErrorTransformer._build_field_path_with_brackets([])
    assert result == ""


def test_build_field_path_with_brackets_single_string():
    result = PydanticErrorTransformer._build_field_path_with_brackets(["model"])
    assert result == "model"


def test_build_field_path_with_brackets_string_then_int():
    result = PydanticErrorTransformer._build_field_path_with_brackets(["states", 0])
    assert result == "states[0]"


def test_build_field_path_with_brackets_multiple_parts():
    result = PydanticErrorTransformer._build_field_path_with_brackets(["mcp_servers", 1, "config", "args"])
    assert result == "mcp_servers[1].config.args"


def test_build_field_path_with_brackets_int_at_start():
    result = PydanticErrorTransformer._build_field_path_with_brackets([0])
    assert result == "[0]"


def test_build_field_path_with_brackets_int_at_start_with_more_parts():
    result = PydanticErrorTransformer._build_field_path_with_brackets([0, "name"])
    assert result == "[0].name"


def test_build_field_path_with_brackets_consecutive_strings():
    result = PydanticErrorTransformer._build_field_path_with_brackets(["retry_policy", "max_interval"])
    assert result == "retry_policy.max_interval"


# ---------------------------------------------------------------------------
# 2. Static method: _extract_relative_path
# ---------------------------------------------------------------------------


def test_extract_relative_path_section_in_middle():
    loc = ["assistants", 0, "system_prompt"]
    result = PydanticErrorTransformer._extract_relative_path(loc, "assistants")
    assert result == "system_prompt"


def test_extract_relative_path_section_at_end_returns_last():
    # Section at end, nothing after index → returns leaf (last element)
    loc = ["states", 0]
    result = PydanticErrorTransformer._extract_relative_path(loc, "states")
    assert result == "0"


def test_extract_relative_path_normal_case_nested():
    loc = ["mcp_servers", 0, "config", "args"]
    result = PydanticErrorTransformer._extract_relative_path(loc, "mcp_servers")
    assert result == "config.args"


def test_extract_relative_path_skips_integer_parts():
    # Integer parts after section+idx should be skipped in dot path
    loc = ["tools", 0, "params", 1, "value"]
    result = PydanticErrorTransformer._extract_relative_path(loc, "tools")
    assert result == "params.value"


def test_extract_relative_path_section_at_list_end_no_path():
    # Only section + int, no field parts → returns last element as string
    loc = ["assistants", 2]
    result = PydanticErrorTransformer._extract_relative_path(loc, "assistants")
    assert result == "2"


# ---------------------------------------------------------------------------
# 3. _extract_field_path
# ---------------------------------------------------------------------------


def test_extract_field_path_empty_loc_list():
    transformer = _make_transformer_with_data({})
    assert transformer._extract_field_path([]) == ""


def test_extract_field_path_single_string():
    transformer = _make_transformer_with_data({})
    assert transformer._extract_field_path(["model"]) == "model"


def test_extract_field_path_states_section_relative():
    transformer = _make_transformer_with_data({})
    result = transformer._extract_field_path(["states", 0, "model"])
    assert result == "model"


def test_extract_field_path_assistants_section_relative():
    transformer = _make_transformer_with_data({})
    result = transformer._extract_field_path(["assistants", 0, "system_prompt"])
    assert result == "system_prompt"


def test_extract_field_path_mcp_servers_section_relative():
    transformer = _make_transformer_with_data({})
    result = transformer._extract_field_path(["mcp_servers", 0, "config", "args"])
    assert result == "config.args"


def test_extract_field_path_top_level():
    transformer = _make_transformer_with_data({})
    result = transformer._extract_field_path(["retry_policy", "max_interval"])
    assert result == "retry_policy.max_interval"


def test_extract_field_path_tools_section_relative():
    transformer = _make_transformer_with_data({})
    result = transformer._extract_field_path(["tools", 0, "name"])
    assert result == "name"


def test_extract_field_path_custom_nodes_section_relative():
    transformer = _make_transformer_with_data({})
    result = transformer._extract_field_path(["custom_nodes", 0, "type"])
    assert result == "type"


def test_extract_field_path_only_ints_returns_last():
    transformer = _make_transformer_with_data({})
    result = transformer._extract_field_path([0, 1, 2])
    assert result == "2"


# ---------------------------------------------------------------------------
# 4. _navigate_yaml_path
# ---------------------------------------------------------------------------

_SAMPLE_YAML_DATA = {
    "states": [{"id": "state_1", "assistant_id": "assistant_1"}],
    "assistants": [{"id": "assistant_1", "mcp_servers": [{"name": "my_mcp"}]}],
}


def test_navigate_yaml_path_empty_path_returns_full_data():
    transformer = _make_transformer_with_data(_SAMPLE_YAML_DATA)
    result = transformer._navigate_yaml_path(())
    assert result is _SAMPLE_YAML_DATA


def test_navigate_yaml_path_valid_path():
    transformer = _make_transformer_with_data(_SAMPLE_YAML_DATA)
    result = transformer._navigate_yaml_path(("states", 0, "id"))
    assert result == "state_1"


def test_navigate_yaml_path_int_index_out_of_bounds():
    transformer = _make_transformer_with_data(_SAMPLE_YAML_DATA)
    result = transformer._navigate_yaml_path(("states", 99, "id"))
    assert result is None


def test_navigate_yaml_path_through_non_dict():
    data = {"states": [{"id": "state_1"}]}
    transformer = _make_transformer_with_data(data)
    # "id" value is a string, trying to go further fails
    result = transformer._navigate_yaml_path(("states", 0, "id", "extra"))
    assert result is None


def test_navigate_yaml_path_none_current():
    data = {"states": [{"model": None}]}
    transformer = _make_transformer_with_data(data)
    result = transformer._navigate_yaml_path(("states", 0, "model", "sub"))
    assert result is None


def test_navigate_yaml_path_missing_key_returns_none():
    transformer = _make_transformer_with_data(_SAMPLE_YAML_DATA)
    result = transformer._navigate_yaml_path(("states", 0, "nonexistent"))
    assert result is None


def test_navigate_yaml_path_nested_mcp():
    transformer = _make_transformer_with_data(_SAMPLE_YAML_DATA)
    result = transformer._navigate_yaml_path(("assistants", 0, "mcp_servers", 0, "name"))
    assert result == "my_mcp"


# ---------------------------------------------------------------------------
# 5. _find_state_by_assistant_id
# ---------------------------------------------------------------------------


def test_find_state_by_assistant_id_found():
    data = {
        "states": [
            {"id": "state_1", "assistant_id": "asst_1"},
            {"id": "state_2", "assistant_id": "asst_2"},
        ]
    }
    transformer = _make_transformer_with_data(data)
    assert transformer._find_state_by_assistant_id("asst_1") == "state_1"
    assert transformer._find_state_by_assistant_id("asst_2") == "state_2"


def test_find_state_by_assistant_id_not_found():
    data = {"states": [{"id": "state_1", "assistant_id": "asst_1"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_state_by_assistant_id("nonexistent") is None


def test_find_state_by_assistant_id_non_dict_state_skipped():
    data = {"states": ["not_a_dict", {"id": "state_1", "assistant_id": "asst_1"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_state_by_assistant_id("asst_1") == "state_1"


def test_find_state_by_assistant_id_empty_states():
    transformer = _make_transformer_with_data({"states": []})
    assert transformer._find_state_by_assistant_id("asst_1") is None


def test_find_state_by_assistant_id_no_states_key():
    transformer = _make_transformer_with_data({})
    assert transformer._find_state_by_assistant_id("asst_1") is None


# ---------------------------------------------------------------------------
# 6. _find_state_by_tool_id
# ---------------------------------------------------------------------------


def test_find_state_by_tool_id_found():
    data = {"states": [{"id": "state_1", "tools": ["tool_a", "tool_b"]}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_state_by_tool_id("tool_a") == "state_1"
    assert transformer._find_state_by_tool_id("tool_b") == "state_1"


def test_find_state_by_tool_id_not_found():
    data = {"states": [{"id": "state_1", "tools": ["tool_a"]}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_state_by_tool_id("tool_z") is None


def test_find_state_by_tool_id_tools_not_a_list():
    data = {"states": [{"id": "state_1", "tools": "not_a_list"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_state_by_tool_id("tool_a") is None


def test_find_state_by_tool_id_non_dict_state_skipped():
    data = {"states": ["not_a_dict", {"id": "state_1", "tools": ["tool_a"]}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_state_by_tool_id("tool_a") == "state_1"


def test_find_state_by_tool_id_empty_states():
    transformer = _make_transformer_with_data({"states": []})
    assert transformer._find_state_by_tool_id("tool_a") is None


# ---------------------------------------------------------------------------
# 7. _find_state_by_node_id
# ---------------------------------------------------------------------------


def test_find_state_by_node_id_found():
    data = {"states": [{"id": "state_1", "node": "my_node"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_state_by_node_id("my_node") == "state_1"


def test_find_state_by_node_id_not_found():
    data = {"states": [{"id": "state_1", "node": "other_node"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_state_by_node_id("my_node") is None


def test_find_state_by_node_id_non_dict_state_skipped():
    data = {"states": ["not_a_dict", {"id": "state_1", "node": "my_node"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_state_by_node_id("my_node") == "state_1"


def test_find_state_by_node_id_empty_states():
    transformer = _make_transformer_with_data({"states": []})
    assert transformer._find_state_by_node_id("my_node") is None


# ---------------------------------------------------------------------------
# 8. _extract_item_id
# ---------------------------------------------------------------------------


def test_extract_item_id_section_not_in_loc_list():
    transformer = _make_transformer_with_data({"assistants": [{"id": "asst_1"}]})
    assert transformer._extract_item_id(["states", 0, "model"], "assistants") is None


def test_extract_item_id_normal_case():
    data = {"assistants": [{"id": "asst_1"}, {"id": "asst_2"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_item_id(["assistants", 0, "model"], "assistants") == "asst_1"
    assert transformer._extract_item_id(["assistants", 1, "model"], "assistants") == "asst_2"


def test_extract_item_id_index_out_of_bounds():
    data = {"assistants": [{"id": "asst_1"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_item_id(["assistants", 5, "model"], "assistants") is None


def test_extract_item_id_item_not_a_dict():
    data = {"assistants": ["not_a_dict"]}
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_item_id(["assistants", 0, "model"], "assistants") is None


def test_extract_item_id_no_id_field():
    data = {"assistants": [{"name": "no_id_here"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_item_id(["assistants", 0, "model"], "assistants") is None


def test_extract_item_id_no_int_after_section():
    # No integer index after the section name
    data = {"assistants": [{"id": "asst_1"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_item_id(["assistants", "model"], "assistants") is None


# ---------------------------------------------------------------------------
# 9. _find_assistant_by_nested_item
# ---------------------------------------------------------------------------


def test_find_assistant_by_nested_item_yaml_data_is_none():
    transformer = _make_transformer_with_data({})
    transformer._yaml_data = None
    assert transformer._find_assistant_by_nested_item("mcp_servers", ["mcp_servers", 0]) is None


def test_find_assistant_by_nested_item_section_not_in_loc():
    data = {"assistants": [{"id": "asst_1", "mcp_servers": [{"name": "mcp_1"}]}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_assistant_by_nested_item("mcp_servers", ["states", 0]) is None


def test_find_assistant_by_nested_item_found():
    data = {
        "assistants": [
            {"id": "asst_1", "mcp_servers": [{"name": "mcp_a"}, {"name": "mcp_b"}]},
        ]
    }
    transformer = _make_transformer_with_data(data)
    # Index 0 exists in asst_1's mcp_servers
    result = transformer._find_assistant_by_nested_item("mcp_servers", ["mcp_servers", 0, "config"])
    assert result == "asst_1"


def test_find_assistant_by_nested_item_index_out_of_bounds():
    data = {"assistants": [{"id": "asst_1", "mcp_servers": [{"name": "mcp_a"}]}]}
    transformer = _make_transformer_with_data(data)
    result = transformer._find_assistant_by_nested_item("mcp_servers", ["mcp_servers", 5])
    assert result is None


def test_find_assistant_by_nested_item_non_dict_assistant_skipped():
    data = {
        "assistants": [
            "not_a_dict",
            {"id": "asst_2", "mcp_servers": [{"name": "mcp_b"}]},
        ]
    }
    transformer = _make_transformer_with_data(data)
    result = transformer._find_assistant_by_nested_item("mcp_servers", ["mcp_servers", 0])
    assert result == "asst_2"


def test_find_assistant_by_nested_item_no_int_after_section():
    data = {"assistants": [{"id": "asst_1", "mcp_servers": [{"name": "mcp_a"}]}]}
    transformer = _make_transformer_with_data(data)
    result = transformer._find_assistant_by_nested_item("mcp_servers", ["mcp_servers"])
    assert result is None


# ---------------------------------------------------------------------------
# 10. _find_mcp_name_in_assistants
# ---------------------------------------------------------------------------


def test_find_mcp_name_in_assistants_found():
    data = {
        "assistants": [
            {"id": "asst_1", "mcp_servers": [{"name": "server_alpha"}, {"name": "server_beta"}]},
        ]
    }
    transformer = _make_transformer_with_data(data)
    assert transformer._find_mcp_name_in_assistants(0) == "server_alpha"
    assert transformer._find_mcp_name_in_assistants(1) == "server_beta"


def test_find_mcp_name_in_assistants_server_idx_out_of_bounds():
    data = {"assistants": [{"id": "asst_1", "mcp_servers": [{"name": "server_alpha"}]}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_mcp_name_in_assistants(5) is None


def test_find_mcp_name_in_assistants_no_name_field():
    data = {"assistants": [{"id": "asst_1", "mcp_servers": [{"type": "stdio"}]}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_mcp_name_in_assistants(0) is None


def test_find_mcp_name_in_assistants_empty_assistants():
    transformer = _make_transformer_with_data({"assistants": []})
    assert transformer._find_mcp_name_in_assistants(0) is None


def test_find_mcp_name_in_assistants_non_dict_assistant_skipped():
    data = {
        "assistants": [
            "not_a_dict",
            {"id": "asst_2", "mcp_servers": [{"name": "server_gamma"}]},
        ]
    }
    transformer = _make_transformer_with_data(data)
    assert transformer._find_mcp_name_in_assistants(0) == "server_gamma"


def test_find_mcp_name_in_assistants_mcp_server_not_a_dict():
    data = {"assistants": [{"id": "asst_1", "mcp_servers": ["not_a_dict"]}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._find_mcp_name_in_assistants(0) is None


# ---------------------------------------------------------------------------
# 11. _extract_mcp_meta
# ---------------------------------------------------------------------------


def test_extract_mcp_meta_yaml_data_is_none():
    transformer = _make_transformer_with_data({})
    transformer._yaml_data = None
    assert transformer._extract_mcp_meta(["mcp_servers", 0]) is None


def test_extract_mcp_meta_mcp_servers_not_in_loc():
    data = {"assistants": [{"id": "asst_1"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_mcp_meta(["states", 0, "model"]) is None


def test_extract_mcp_meta_mcp_server_found_with_name():
    data = {
        "assistants": [
            {
                "id": "asst_1",
                "mcp_servers": [{"name": "my_server", "type": "stdio"}],
            }
        ]
    }
    transformer = _make_transformer_with_data(data)
    loc = ["assistants", 0, "mcp_servers", 0, "config"]
    result = transformer._extract_mcp_meta(loc)
    assert result == MCPMeta(mcp_name="my_server")


def test_extract_mcp_meta_mcp_server_no_name_searches_assistants():
    # Relative path (no assistants in loc), server without name at direct path,
    # but found by searching all assistants
    data = {
        "assistants": [
            {"id": "asst_1", "mcp_servers": [{"name": "found_via_search"}]},
        ]
    }
    transformer = _make_transformer_with_data(data)
    # Only mcp_servers in loc (relative path), no assistants key
    loc = ["mcp_servers", 0, "config"]
    result = transformer._extract_mcp_meta(loc)
    assert result == MCPMeta(mcp_name="found_via_search")


def test_extract_mcp_meta_no_int_after_mcp_servers():
    transformer = _make_transformer_with_data({"mcp_servers": []})
    assert transformer._extract_mcp_meta(["mcp_servers", "config"]) is None


def test_extract_mcp_meta_server_idx_out_of_bounds_returns_none():
    data = {"assistants": [], "mcp_servers": []}
    transformer = _make_transformer_with_data(data)
    loc = ["mcp_servers", 99, "config"]
    result = transformer._extract_mcp_meta(loc)
    assert result is None


# ---------------------------------------------------------------------------
# 12. _extract_state_id
# ---------------------------------------------------------------------------


def test_extract_state_id_empty_loc_list():
    transformer = _make_transformer_with_data({"states": [{"id": "state_1"}]})
    assert transformer._extract_state_id([]) is None


def test_extract_state_id_yaml_data_is_none():
    transformer = _make_transformer_with_data({})
    transformer._yaml_data = None
    assert transformer._extract_state_id(["states", 0, "model"]) is None


def test_extract_state_id_from_states_section():
    data = {"states": [{"id": "state_1", "assistant_id": "asst_1"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_state_id(["states", 0, "model"]) == "state_1"


def test_extract_state_id_states_index_out_of_bounds():
    data = {"states": [{"id": "state_1"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_state_id(["states", 5, "model"]) is None


def test_extract_state_id_from_assistants_section():
    data = {
        "assistants": [{"id": "asst_1"}],
        "states": [{"id": "state_1", "assistant_id": "asst_1"}],
    }
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_state_id(["assistants", 0, "system_prompt"]) == "state_1"


def test_extract_state_id_from_tools_section():
    data = {
        "tools": [{"id": "tool_1"}],
        "states": [{"id": "state_1", "tools": ["tool_1"]}],
    }
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_state_id(["tools", 0, "name"]) == "state_1"


def test_extract_state_id_from_custom_nodes_section():
    data = {
        "custom_nodes": [{"id": "node_1"}],
        "states": [{"id": "state_1", "node": "node_1"}],
    }
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_state_id(["custom_nodes", 0, "type"]) == "state_1"


def test_extract_state_id_mcp_servers_relative_path():
    # mcp_servers in loc but not assistants → relative path
    data = {
        "assistants": [{"id": "asst_1", "mcp_servers": [{"name": "mcp_1"}]}],
        "states": [{"id": "state_1", "assistant_id": "asst_1"}],
    }
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_state_id(["mcp_servers", 0, "config"]) == "state_1"


def test_extract_state_id_no_matching_section():
    data = {"states": [{"id": "state_1"}]}
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_state_id(["retry_policy", "max_interval"]) is None


def test_extract_state_id_assistants_section_no_matching_state():
    data = {
        "assistants": [{"id": "asst_orphan"}],
        "states": [{"id": "state_1", "assistant_id": "other_asst"}],
    }
    transformer = _make_transformer_with_data(data)
    assert transformer._extract_state_id(["assistants", 0, "model"]) is None


# ---------------------------------------------------------------------------
# 13. _lookup_line_for_section
# ---------------------------------------------------------------------------


def test_lookup_line_for_section_section_not_in_loc():
    transformer = _make_transformer_with_data({"states": [{"id": "state_1"}]})
    result = transformer._lookup_line_for_section(["assistants", 0, "model"], "states", "find_line_for_state_field")
    assert result is None


def test_lookup_line_for_section_no_int_after_section():
    transformer = _make_transformer_with_data({"states": [{"id": "state_1"}]})
    result = transformer._lookup_line_for_section(["states", "model"], "states", "find_line_for_state_field")
    assert result is None


def test_lookup_line_for_section_index_out_of_bounds():
    transformer = _make_transformer_with_data({"states": [{"id": "state_1"}]})
    result = transformer._lookup_line_for_section(["states", 5, "model"], "states", "find_line_for_state_field")
    assert result is None


def test_lookup_line_for_section_item_not_a_dict():
    transformer = _make_transformer_with_data({"states": ["not_a_dict"]})
    result = transformer._lookup_line_for_section(["states", 0, "model"], "states", "find_line_for_state_field")
    assert result is None


def test_lookup_line_for_section_no_id():
    transformer = _make_transformer_with_data({"states": [{"model": "gpt-4"}]})
    result = transformer._lookup_line_for_section(["states", 0, "model"], "states", "find_line_for_state_field")
    assert result is None


def test_lookup_line_for_section_happy_path():
    data = {"states": [{"id": "state_1", "model": "gpt-4"}]}
    transformer = _make_transformer_with_data(data)
    transformer._line_finder.find_line_for_state_field.return_value = 42
    result = transformer._lookup_line_for_section(["states", 0, "model"], "states", "find_line_for_state_field")
    assert result == 42
    transformer._line_finder.find_line_for_state_field.assert_called_once_with("state_1", "model")


def test_lookup_line_for_section_attribute_error_returns_none():
    # Setting _yaml_data to a non-dict triggers AttributeError on .get() call,
    # which is caught by the except clause and returns None.
    transformer = _make_transformer_with_data({"states": [{"id": "state_1"}]})
    transformer._yaml_data = "not_a_dict"
    result = transformer._lookup_line_for_section(["states", 0, "model"], "states", "find_line_for_state_field")
    assert result is None


# ---------------------------------------------------------------------------
# 14. _extract_line_number
# ---------------------------------------------------------------------------


def test_extract_line_number_no_line_finder():
    transformer = _make_transformer_with_data({"states": [{"id": "state_1"}]})
    transformer._line_finder = None
    assert transformer._extract_line_number(["states", 0, "model"]) is None


def test_extract_line_number_empty_loc_list():
    transformer = _make_transformer_with_data({})
    assert transformer._extract_line_number([]) is None


def test_extract_line_number_mcp_server_path_with_assistant():
    data = {
        "assistants": [{"id": "asst_1", "mcp_servers": [{"name": "mcp_1"}]}],
    }
    transformer = _make_transformer_with_data(data)
    transformer._line_finder.find_line_for_assistant_field.return_value = 15
    result = transformer._extract_line_number(["assistants", 0, "mcp_servers", 0, "config"])
    assert result == 15


def test_extract_line_number_states_section():
    data = {"states": [{"id": "state_1", "model": "gpt-4"}]}
    transformer = _make_transformer_with_data(data)
    transformer._line_finder.find_line_for_state_field.return_value = 7
    result = transformer._extract_line_number(["states", 0, "model"])
    assert result == 7


def test_extract_line_number_assistants_section():
    data = {"assistants": [{"id": "asst_1", "system_prompt": "hello"}]}
    transformer = _make_transformer_with_data(data)
    transformer._line_finder.find_line_for_assistant_field.return_value = 5
    result = transformer._extract_line_number(["assistants", 0, "system_prompt"])
    assert result == 5


def test_extract_line_number_top_level_field():
    transformer = _make_transformer_with_data({})
    transformer._line_finder.find_line_for_top_level_field.return_value = 3
    result = transformer._extract_line_number(["retry_policy", "max_interval"])
    assert result == 3
    transformer._line_finder.find_line_for_top_level_field.assert_called_with("retry_policy.max_interval")


def test_extract_line_number_top_level_fallback_parent_path():
    transformer = _make_transformer_with_data({})
    # First call (full path) returns None, second call (parent) returns line number
    transformer._line_finder.find_line_for_top_level_field.side_effect = [None, 2]
    result = transformer._extract_line_number(["retry_policy", "max_interval"])
    assert result == 2
    assert transformer._line_finder.find_line_for_top_level_field.call_count == 2


def test_extract_line_number_top_level_no_fallback_when_no_dot():
    transformer = _make_transformer_with_data({})
    transformer._line_finder.find_line_for_top_level_field.return_value = None
    result = transformer._extract_line_number(["max_concurrency"])
    assert result is None
    # Only one call when path has no dot → no parent fallback
    transformer._line_finder.find_line_for_top_level_field.assert_called_once()


# ---------------------------------------------------------------------------
# 15. transform (integration-style)
# ---------------------------------------------------------------------------

_TRANSFORM_YAML = """
assistants:
  - id: assistant_1
    assistant_id: uuid
states:
  - id: state_1
    assistant_id: assistant_1
"""


def _make_transform_transformer(errors: list[dict], yaml_config: str = _TRANSFORM_YAML) -> PydanticErrorTransformer:
    mock_workflow_config = MagicMock()
    mock_workflow_config.yaml_config = yaml_config
    mock_validation_error = MagicMock()
    mock_validation_error.errors.return_value = errors
    return PydanticErrorTransformer(mock_validation_error, mock_workflow_config)


@patch("codemie.workflows.validation.transformers.extract_line_numbers", return_value={})
@patch("codemie.workflows.validation.transformers.YamlLineFinder")
def test_transform_single_error_basic(mock_yaml_line_finder, mock_extract_line_numbers):
    mock_yaml_line_finder.return_value = MagicMock()
    transformer = _make_transform_transformer(
        [{"loc": ("states", 0, "model"), "msg": "Field required", "type": "missing"}]
    )

    result = transformer.transform()

    assert len(result) == 1
    error = result[0]
    assert error["message"] == "Validation error"
    assert error["path"] == "model"
    assert error["details"] == "Field required"
    assert "id" in error
    assert error["state_id"] == "state_1"


@patch("codemie.workflows.validation.transformers.extract_line_numbers", return_value={})
@patch("codemie.workflows.validation.transformers.YamlLineFinder")
def test_transform_with_explicit_state_id(mock_yaml_line_finder, mock_extract_line_numbers):
    mock_yaml_line_finder.return_value = MagicMock()
    transformer = _make_transform_transformer(
        [{"loc": ("states", 0, "model"), "msg": "Field required", "type": "missing"}]
    )

    result = transformer.transform(state_id="custom_state")

    assert result[0]["state_id"] == "custom_state"


@patch("codemie.workflows.validation.transformers.extract_line_numbers", return_value={})
@patch("codemie.workflows.validation.transformers.YamlLineFinder")
def test_transform_multiple_errors(mock_yaml_line_finder, mock_extract_line_numbers):
    mock_yaml_line_finder.return_value = MagicMock()
    errors = [
        {"loc": ("states", 0, "model"), "msg": "Field required", "type": "missing"},
        {"loc": ("assistants", 0, "system_prompt"), "msg": "Value error", "type": "value_error"},
    ]
    transformer = _make_transform_transformer(errors)

    result = transformer.transform()

    assert len(result) == 2
    assert all(r["message"] == "Validation error" for r in result)


@patch("codemie.workflows.validation.transformers.extract_line_numbers", return_value={})
@patch("codemie.workflows.validation.transformers.YamlLineFinder")
def test_transform_empty_errors(mock_yaml_line_finder, mock_extract_line_numbers):
    mock_yaml_line_finder.return_value = MagicMock()
    transformer = _make_transform_transformer([])

    result = transformer.transform()

    assert result == []


@patch("codemie.workflows.validation.transformers.extract_line_numbers", return_value={})
@patch("codemie.workflows.validation.transformers.YamlLineFinder")
def test_transform_excludes_none_fields(mock_yaml_line_finder, mock_extract_line_numbers):
    mock_finder = MagicMock()
    mock_finder.find_line_for_state_field.return_value = None
    mock_finder.find_line_for_top_level_field.return_value = None
    mock_yaml_line_finder.return_value = mock_finder
    transformer = _make_transform_transformer(
        [{"loc": ("states", 0, "model"), "msg": "Field required", "type": "missing"}]
    )

    result = transformer.transform()

    # config_line should be excluded when None
    assert "config_line" not in result[0]


@patch("codemie.workflows.validation.transformers.extract_line_numbers", return_value={})
@patch("codemie.workflows.validation.transformers.YamlLineFinder")
def test_transform_state_id_explicit_overrides_extraction(mock_yaml_line_finder, mock_extract_line_numbers):
    """When state_id is explicitly passed, all errors get that state_id."""
    mock_yaml_line_finder.return_value = MagicMock()
    errors = [
        {"loc": ("states", 0, "model"), "msg": "err1", "type": "missing"},
        {"loc": ("states", 0, "assistant_id"), "msg": "err2", "type": "missing"},
    ]
    transformer = _make_transform_transformer(errors)

    result = transformer.transform(state_id="forced_state")

    assert all(r["state_id"] == "forced_state" for r in result)


@patch("codemie.workflows.validation.transformers.extract_line_numbers", return_value={})
@patch("codemie.workflows.validation.transformers.YamlLineFinder")
def test_transform_result_has_unique_ids(mock_yaml_line_finder, mock_extract_line_numbers):
    """Each error should have a unique UUID id."""
    mock_yaml_line_finder.return_value = MagicMock()
    errors = [
        {"loc": ("states", 0, "model"), "msg": "err1", "type": "missing"},
        {"loc": ("states", 0, "assistant_id"), "msg": "err2", "type": "missing"},
    ]
    transformer = _make_transform_transformer(errors)

    result = transformer.transform()

    ids = [r["id"] for r in result]
    assert len(ids) == len(set(ids)), "All error IDs must be unique"


@patch("codemie.workflows.validation.transformers.extract_line_numbers", return_value={})
@patch("codemie.workflows.validation.transformers.YamlLineFinder")
def test_transform_top_level_error_has_no_state_id(mock_yaml_line_finder, mock_extract_line_numbers):
    """Top-level errors (e.g. retry_policy) should not have a state_id."""
    mock_yaml_line_finder.return_value = MagicMock()
    transformer = _make_transform_transformer(
        [{"loc": ("retry_policy", "max_interval"), "msg": "Value too large", "type": "value_error"}]
    )

    result = transformer.transform()

    assert "state_id" not in result[0]
