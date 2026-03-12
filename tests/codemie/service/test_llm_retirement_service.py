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

from unittest.mock import MagicMock

import yaml

from codemie.service.llm_retirement_service import (
    _LiteralBlockDumper,
    _replace_model_in_yaml,
    _str_representer,
    LLMRetirementService,
)


class TestReplaceModelInYaml:
    def test_replaces_model_key_at_top_level(self):
        node = {"model": "old-model", "temperature": 0.5}
        changed = _replace_model_in_yaml(node, "old-model", "new-model")
        assert changed is True
        assert node["model"] == "new-model"

    def test_replaces_model_key_nested_in_dict(self):
        node = {"assistant": {"model": "old-model", "name": "bot"}}
        changed = _replace_model_in_yaml(node, "old-model", "new-model")
        assert changed is True
        assert node["assistant"]["model"] == "new-model"

    def test_replaces_model_key_in_list_items(self):
        node = {"assistants": [{"model": "old-model"}, {"model": "keep-model"}]}
        changed = _replace_model_in_yaml(node, "old-model", "new-model")
        assert changed is True
        assert node["assistants"][0]["model"] == "new-model"
        assert node["assistants"][1]["model"] == "keep-model"

    def test_no_change_when_model_not_present(self):
        node = {"name": "workflow", "type": "supervisor"}
        changed = _replace_model_in_yaml(node, "old-model", "new-model")
        assert changed is False

    def test_no_change_when_model_value_does_not_match(self):
        node = {"model": "other-model"}
        changed = _replace_model_in_yaml(node, "old-model", "new-model")
        assert changed is False
        assert node["model"] == "other-model"

    def test_does_not_replace_non_model_keys_containing_model_name(self):
        node = {"model_name": "old-model", "model": "old-model"}
        changed = _replace_model_in_yaml(node, "old-model", "new-model")
        assert changed is True
        assert node["model_name"] == "old-model"  # untouched
        assert node["model"] == "new-model"

    def test_handles_deeply_nested_structure(self):
        node = {"states": [{"nodes": [{"config": {"model": "old-model"}}]}]}
        changed = _replace_model_in_yaml(node, "old-model", "new-model")
        assert changed is True
        assert node["states"][0]["nodes"][0]["config"]["model"] == "new-model"

    def test_handles_scalar_node_gracefully(self):
        changed = _replace_model_in_yaml("just-a-string", "old-model", "new-model")
        assert changed is False

    def test_handles_none_node_gracefully(self):
        changed = _replace_model_in_yaml(None, "old-model", "new-model")
        assert changed is False


class TestLiteralBlockDumper:
    def test_multiline_string_uses_block_scalar_style(self):
        data = {"prompt": "Hello\nWorld\nFoo"}
        result = yaml.dump(data, Dumper=_LiteralBlockDumper, default_flow_style=False)
        assert "|-\n" in result or "|\n" in result
        assert "\\n" not in result

    def test_single_line_string_uses_plain_style(self):
        data = {"model": "gpt-4"}
        result = yaml.dump(data, Dumper=_LiteralBlockDumper, default_flow_style=False)
        assert "model: gpt-4" in result
        assert "|" not in result

    def test_round_trip_preserves_multiline_content(self):
        original = "Step one\nStep two\nStep three"
        data = {"system_prompt": original}
        dumped = yaml.dump(data, Dumper=_LiteralBlockDumper, default_flow_style=False)
        reloaded = yaml.safe_load(dumped)
        assert reloaded["system_prompt"] == original

    def test_round_trip_preserves_nested_multiline(self):
        original_prompt = "You are helpful.\nBe concise."
        data = {"assistants": [{"model": "gpt-4", "system_prompt": original_prompt}]}
        dumped = yaml.dump(data, Dumper=_LiteralBlockDumper, default_flow_style=False)
        reloaded = yaml.safe_load(dumped)
        assert reloaded["assistants"][0]["system_prompt"] == original_prompt

    def test_safe_dumper_contrast_uses_escaped_newlines(self):
        """Confirm the bug we fix: plain safe_dump escapes newlines."""
        data = {"prompt": "line1\nline2"}
        safe_result = yaml.safe_dump(data, default_flow_style=False)
        assert "\\n" in safe_result or '"\n' not in safe_result

    def test_str_representer_block_style_for_newline(self):
        dumper = _LiteralBlockDumper("")
        node = _str_representer(dumper, "line1\nline2")
        assert node.style == "|"

    def test_str_representer_plain_style_for_single_line(self):
        dumper = _LiteralBlockDumper("")
        node = _str_representer(dumper, "single-line")
        assert node.style != "|"


class TestUpdateYamlConfigField:
    def _make_workflow(self, yaml_config: str | None) -> MagicMock:
        workflow = MagicMock()
        workflow.yaml_config = yaml_config
        return workflow

    def test_returns_false_when_yaml_config_is_none(self):
        workflow = self._make_workflow(None)
        changed = LLMRetirementService._update_yaml_config_field(workflow, "old", "new")
        assert changed is False

    def test_returns_false_when_deprecated_model_not_in_yaml(self):
        yaml_config = yaml.safe_dump({"model": "other-model"})
        workflow = self._make_workflow(yaml_config)
        changed = LLMRetirementService._update_yaml_config_field(workflow, "old-model", "new-model")
        assert changed is False

    def test_returns_true_and_updates_yaml_when_model_replaced(self):
        original = {"assistants": [{"model": "old-model", "system_prompt": "Be helpful."}]}
        yaml_config = yaml.safe_dump(original)
        workflow = self._make_workflow(yaml_config)

        changed = LLMRetirementService._update_yaml_config_field(workflow, "old-model", "new-model")

        assert changed is True
        reloaded = yaml.safe_load(workflow.yaml_config)
        assert reloaded["assistants"][0]["model"] == "new-model"

    def test_multiline_prompts_use_block_scalar_after_retirement(self):
        original = {"assistants": [{"model": "old-model", "system_prompt": "Step one\nStep two\nStep three"}]}
        yaml_config = yaml.safe_dump(original)
        workflow = self._make_workflow(yaml_config)

        LLMRetirementService._update_yaml_config_field(workflow, "old-model", "new-model")

        assert "\\n" not in workflow.yaml_config
        reloaded = yaml.safe_load(workflow.yaml_config)
        assert reloaded["assistants"][0]["system_prompt"] == "Step one\nStep two\nStep three"

    def test_returns_false_when_model_key_present_but_value_differs(self):
        yaml_config = yaml.safe_dump({"model": "different-model"})
        workflow = self._make_workflow(yaml_config)
        changed = LLMRetirementService._update_yaml_config_field(workflow, "old-model", "new-model")
        assert changed is False

    def test_deprecated_model_no_longer_present_in_yaml_after_update(self):
        """After retirement the deprecated model name must not appear anywhere in the stored YAML."""
        original = {
            "assistants": [
                {"name": "coder", "model": "deprecated-gpt-4", "system_prompt": "Write code."},
                {"name": "reviewer", "model": "keep-model", "system_prompt": "Review code."},
            ],
            "states": [
                {"name": "start", "config": {"model": "deprecated-gpt-4"}},
            ],
        }
        yaml_config = yaml.safe_dump(original)
        workflow = self._make_workflow(yaml_config)

        changed = LLMRetirementService._update_yaml_config_field(workflow, "deprecated-gpt-4", "gpt-4o")

        assert changed is True
        assert "deprecated-gpt-4" not in workflow.yaml_config
        reloaded = yaml.safe_load(workflow.yaml_config)
        assert reloaded["assistants"][0]["model"] == "gpt-4o"
        assert reloaded["assistants"][1]["model"] == "keep-model"
        assert reloaded["states"][0]["config"]["model"] == "gpt-4o"
