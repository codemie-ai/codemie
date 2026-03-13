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

from unittest.mock import MagicMock, call

import pytest
import yaml

from codemie.service.llm_retirement_service import (
    _LiteralBlockDumper,
    _replace_model_in_yaml,
    _str_representer,
    LLMRetirementService,
)

# Shared constants used in TestRetireWorkflows parametrize (must be module-level so
# they are evaluated at decoration time).
_BROKEN_YAML = "model: deprecated-gpt-4\n  bad_indent: {unclosed"
_VALID_YAML = yaml.safe_dump({"model": "deprecated-gpt-4"})


class TestReplaceModelInYaml:
    @pytest.mark.parametrize(
        "node, deprecated, replacement, expected_changed, expected_node",
        [
            # --- replacements happen ---
            (
                {"model": "old", "temperature": 0.5},
                "old",
                "new",
                True,
                {"model": "new", "temperature": 0.5},
            ),
            (
                {"assistant": {"model": "old", "name": "bot"}},
                "old",
                "new",
                True,
                {"assistant": {"model": "new", "name": "bot"}},
            ),
            (
                {"assistants": [{"model": "old"}, {"model": "keep"}]},
                "old",
                "new",
                True,
                {"assistants": [{"model": "new"}, {"model": "keep"}]},
            ),
            (
                {"states": [{"nodes": [{"config": {"model": "old"}}]}]},
                "old",
                "new",
                True,
                {"states": [{"nodes": [{"config": {"model": "new"}}]}]},
            ),
            # non-`model` key with same value is left untouched
            (
                {"model_name": "old", "model": "old"},
                "old",
                "new",
                True,
                {"model_name": "old", "model": "new"},
            ),
            # --- no replacement ---
            (
                {"name": "workflow", "type": "supervisor"},
                "old",
                "new",
                False,
                {"name": "workflow", "type": "supervisor"},
            ),
            ({"model": "other"}, "old", "new", False, {"model": "other"}),
            ("just-a-string", "old", "new", False, "just-a-string"),
            (None, "old", "new", False, None),
        ],
    )
    def test_replace_model_in_yaml(self, node, deprecated, replacement, expected_changed, expected_node):
        changed = _replace_model_in_yaml(node, deprecated, replacement)
        assert changed is expected_changed
        assert node == expected_node


class TestLiteralBlockDumper:
    @pytest.mark.parametrize(
        "data, expect_block_scalar",
        [
            ({"prompt": "Hello\nWorld\nFoo"}, True),
            ({"model": "gpt-4"}, False),
        ],
    )
    def test_dump_style(self, data, expect_block_scalar):
        result = yaml.dump(data, Dumper=_LiteralBlockDumper, default_flow_style=False)
        if expect_block_scalar:
            assert "|\n" in result or "|-\n" in result
            assert "\\n" not in result
        else:
            assert "|" not in result

    @pytest.mark.parametrize(
        "data",
        [
            {"system_prompt": "Step one\nStep two\nStep three"},
            {"assistants": [{"model": "gpt-4", "system_prompt": "You are helpful.\nBe concise."}]},
        ],
    )
    def test_round_trip_preserves_content(self, data):
        dumped = yaml.dump(data, Dumper=_LiteralBlockDumper, default_flow_style=False)
        assert yaml.safe_load(dumped) == data

    @pytest.mark.parametrize(
        "text, expect_block",
        [
            ("line1\nline2", True),
            ("single-line", False),
        ],
    )
    def test_str_representer_style(self, text, expect_block):
        dumper = _LiteralBlockDumper("")
        node = _str_representer(dumper, text)
        assert (node.style == "|") is expect_block

    def test_safe_dumper_contrast_uses_escaped_newlines(self):
        """Confirm the bug we fix: plain safe_dump escapes newlines."""
        data = {"prompt": "line1\nline2"}
        safe_result = yaml.safe_dump(data, default_flow_style=False)
        assert "\\n" in safe_result or '"\n' not in safe_result


class TestUpdateYamlConfigField:
    def _make_workflow(self, yaml_config: str | None) -> MagicMock:
        wf = MagicMock()
        wf.yaml_config = yaml_config
        return wf

    @pytest.mark.parametrize(
        "yaml_config",
        [
            pytest.param(None, id="none"),
            pytest.param(yaml.safe_dump({"model": "other-model"}), id="deprecated_absent_from_string"),
            # deprecated appears in string but not as an exact `model` value → _replace_model_in_yaml returns False
            pytest.param(
                yaml.safe_dump({"model": "different-model", "name": "old-model-workflow"}),
                id="model_value_differs",
            ),
        ],
    )
    def test_returns_false(self, yaml_config):
        wf = self._make_workflow(yaml_config)
        assert LLMRetirementService._update_yaml_config_field(wf, "old-model", "new-model") is False

    @pytest.mark.parametrize(
        "original, deprecated, replacement, validate",
        [
            pytest.param(
                {"assistants": [{"model": "old-model", "system_prompt": "Be helpful."}]},
                "old-model",
                "new-model",
                lambda wf: yaml.safe_load(wf.yaml_config)["assistants"][0]["model"] == "new-model",
                id="basic_replacement",
            ),
            pytest.param(
                {"assistants": [{"model": "old-model", "system_prompt": "Step one\nStep two\nStep three"}]},
                "old-model",
                "new-model",
                lambda wf: (
                    "\\n" not in wf.yaml_config
                    and yaml.safe_load(wf.yaml_config)["assistants"][0]["system_prompt"]
                    == "Step one\nStep two\nStep three"
                ),
                id="multiline_block_scalar",
            ),
            pytest.param(
                {
                    "assistants": [
                        {"name": "coder", "model": "deprecated-gpt-4", "system_prompt": "Write code."},
                        {"name": "reviewer", "model": "keep-model", "system_prompt": "Review code."},
                    ],
                    "states": [{"name": "start", "config": {"model": "deprecated-gpt-4"}}],
                },
                "deprecated-gpt-4",
                "gpt-4.1",
                lambda wf: "deprecated-gpt-4" not in wf.yaml_config,
                id="all_occurrences_replaced",
            ),
        ],
    )
    def test_returns_true_and_updates(self, original, deprecated, replacement, validate):
        wf = self._make_workflow(yaml.safe_dump(original))
        assert LLMRetirementService._update_yaml_config_field(wf, deprecated, replacement) is True
        assert validate(wf)

    def test_raises_yaml_error_for_broken_yaml_config(self):
        # Broken YAML that still contains the deprecated model string so the early-exit
        # guard passes, ensuring yaml.safe_load() is actually reached and raises.
        wf = self._make_workflow("model: old-model\n  bad_indent: {unclosed")
        with pytest.raises(yaml.YAMLError):
            LLMRetirementService._update_yaml_config_field(wf, "old-model", "new-model")


class TestRetireWorkflows:
    def _make_session(self, workflows: list) -> MagicMock:
        session = MagicMock()
        session.exec.return_value.all.return_value = workflows
        return session

    def _make_workflow(self, wf_id: int, name: str, yaml_config: str | None) -> MagicMock:
        wf = MagicMock()
        wf.id = wf_id
        wf.name = name
        wf.yaml_config = yaml_config
        wf.assistants = []  # skip assistants-field path; focus on YAML path
        return wf

    @pytest.mark.parametrize(
        "yaml_configs, exp_updated, exp_skipped",
        [
            pytest.param([_VALID_YAML, _BROKEN_YAML], 1, 1, id="one_valid_one_broken"),
            pytest.param([_BROKEN_YAML, _BROKEN_YAML], 0, 2, id="all_broken"),
            pytest.param([], 0, 0, id="no_workflows"),
        ],
    )
    def test_retire_workflows_counts(self, yaml_configs, exp_updated, exp_skipped):
        workflows = [self._make_workflow(i + 1, f"wf{i + 1}", yc) for i, yc in enumerate(yaml_configs)]
        session = self._make_session(workflows)

        updated, skipped = LLMRetirementService._retire_workflows(session, "deprecated-gpt-4", "gpt-4o")

        assert updated == exp_updated
        assert skipped == exp_skipped
        assert session.add.call_count == exp_updated
        assert session.expunge.call_count == exp_skipped

    def test_broken_workflow_is_expunged_before_valid_workflow_is_added(self):
        """expunge(broken) must happen so the broken workflow's dirty state is never flushed."""
        wf_broken = self._make_workflow(1, "broken", _BROKEN_YAML)
        wf_valid = self._make_workflow(2, "valid", _VALID_YAML)
        session = self._make_session([wf_broken, wf_valid])

        LLMRetirementService._retire_workflows(session, "deprecated-gpt-4", "gpt-4.1")

        assert session.mock_calls.index(call.expunge(wf_broken)) < session.mock_calls.index(call.add(wf_valid))
