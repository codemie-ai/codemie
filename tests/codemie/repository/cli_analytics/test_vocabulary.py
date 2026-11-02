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

import pytest

from codemie.repository.cli_analytics import vocabulary as v


def test_supported_span_names_map_to_canonical_kinds():
    assert v.SPAN_KIND == {
        "claude_code.tool": v.SpanKind.TOOL,
        "claude_code.tool.execution": v.SpanKind.TOOL_EXECUTION,
        "claude_code.interaction": v.SpanKind.INTERACTION,
        "claude_code.llm_request": v.SpanKind.LLM_REQUEST,
        "cursor.tool": v.SpanKind.TOOL,
        "cursor.tool.execution": v.SpanKind.TOOL_EXECUTION,
        "cursor.interaction": v.SpanKind.INTERACTION,
    }


def test_claude_code_log_events_map_to_canonical_kinds():
    assert v.EVENT_KIND["api_request"] == v.EventKind.API_REQUEST
    assert v.EVENT_KIND["user_prompt"] == v.EventKind.USER_PROMPT
    assert v.EVENT_KIND["skill_activated"] == v.EventKind.SKILL_ACTIVATED


def test_unknown_names_have_kind_zero():
    assert v.SpanKind.OTHER == 0
    assert v.EventKind.OTHER == 0


def test_known_hook_keys_cover_every_typed_column():
    assert {"type", "timestamp", "session_id", "prompt_id", *v.HOOK_TEXT_FIELDS} <= v.HOOK_KNOWN_KEYS


def test_rollup_metrics_cover_lines_and_active_time_for_supported_harnesses():
    assert v.ROLLUP_METRICS == (
        "claude_code.lines_of_code.count",
        "cursor.lines_of_code.count",
        "claude_code.active_time.total",
        "cursor.active_time.total",
    )


def test_dimension_hook_types_exclude_skill_dispatch():
    # Only these 13 hook types feed the dimensions.
    assert len(v.DIMENSION_HOOK_TYPES) == 13
    assert v.SKILL_DISPATCH_EVENT not in v.DIMENSION_HOOK_TYPES


def test_write_and_edit_tools_exclude_read_only_tools():
    """Read must never count as a file write or edit (EPMCDME-15448): session_files_daily's
    is_written/is_edited gate on tool_name = ANY(WRITE_TOOLS/EDIT_TOOLS), so a Read tool call
    (successful or failed) is excluded from files_changed purely by not being in either list -
    its outcome never even needs to be checked."""
    assert "Read" not in v.WRITE_TOOLS
    assert "Read" not in v.EDIT_TOOLS


def test_known_harness_uses_its_override_label():
    assert v.humanize_harness_name("claude-code") == "Claude Code"


def test_codemie_claude_harness_uses_its_override_label():
    assert v.humanize_harness_name("codemie-claude") == "CodeMie Claude"
    assert v.humanize_harness_name("CODEMIE-CLAUDE") == "CodeMie Claude"


def test_copilot_cli_harness_uses_its_override_label():
    assert v.humanize_harness_name("copilot-cli") == "Copilot CLI"
    assert v.humanize_harness_name("  COPILOT-CLI\u00a0") == "Copilot CLI"


def test_harness_name_is_trimmed_before_the_override_lookup():
    assert v.humanize_harness_name("Claude-Code ") == "Claude Code"
    assert v.humanize_harness_name("  codemie-claude") == "CodeMie Claude"
    assert v.humanize_harness_name("\t\u00a0claude-code\u00a0\n") == "Claude Code"
    assert v.humanize_harness_name("\t\u00a0") == "Unknown"


def test_unmapped_harness_is_generically_humanized():
    assert v.humanize_harness_name("gemini_cli-beta") == "Gemini Cli Beta"


def test_missing_harness_is_unknown():
    assert v.humanize_harness_name(None) == "Unknown"
    assert v.humanize_harness_name("") == "Unknown"
    assert v.humanize_harness_name("   ") == "Unknown"


def test_builtin_command_names_constant():
    """Verify BUILTIN_COMMAND_NAMES is a tuple of exactly ("model", "exit", "quit").

    - No leading slashes (OTel stores without slash)
    - "compact" is absent (it is a real slash command, must be counted)
    - Tuple is immutable
    """
    assert isinstance(v.BUILTIN_COMMAND_NAMES, tuple)
    assert v.BUILTIN_COMMAND_NAMES == ("model", "exit", "quit")
    assert "compact" not in v.BUILTIN_COMMAND_NAMES
    # Verify immutability (attempting to mutate raises TypeError)
    try:
        v.BUILTIN_COMMAND_NAMES[0] = "other"  # type: ignore
        raise AssertionError("Tuple should be immutable")
    except TypeError:
        pass


PARITY_CORPUS: list[str] = [
    '<pasted_content id="b23a">\nStep 1\nStep 2\n</pasted_content id="b23a">\n',
    'Perform the test\n\n<pasted_content id="x1">\nA\n</pasted_content id="x1">\n',
    '<pasted_content id="a">one</pasted_content id="a"> and <pasted_content id="b">two</pasted_content id="b">',
    '<pasted_content id="b23a">\nunterminated text with no close',
    'text</pasted_content id="zz">',
    '<pasted_content id="trunc',
    "<pasted_content",
    "plain typed prompt",
    '<pasted_contentx id="q">not ours</pasted_contentx>',
    "a <pasted_content id=1> b",
    "a <pasted_content\x1cid=1> b",
    "a <pasted_content\x85id=1> b",
    "Why does the title show <pasted_content id=b23a instead of my text?",
    'Why does the title show <pasted_content id="b23a instead of my text?',
    "see <pasted_content tag for details",
    'a <pasted_content id="x"> b',
    'a <pasted_content id="x" n="y"> b',
    "a </pasted_content> b",
    "a <pasted_content> b",
    '<pasted_content id="a">',
    'hello <pasted_content id="a',
    "hello <pasted_content id=",
    "hello <pasted_content id",
    "hello <pasted_content ",
    "hello <pasted_content",
    "hello <pasted_content\n",
    'hello <pasted_content id="a\n',
]

PARITY_EXPECTED: list[str] = [
    "Step 1\nStep 2",
    "Perform the test\n\n\nA",
    "one and two",
    "unterminated text with no close",
    "text",
    "",
    "",
    "plain typed prompt",
    '<pasted_contentx id="q">not ours</pasted_contentx>',
    "a <pasted_content id=1> b",
    "a <pasted_content\x1cid=1> b",
    "a <pasted_content\x85id=1> b",
    "Why does the title show <pasted_content id=b23a instead of my text?",
    'Why does the title show <pasted_content id="b23a instead of my text?',
    "see <pasted_content tag for details",
    "a  b",
    "a  b",
    "a  b",
    "a  b",
    "",
    "hello",
    "hello",
    "hello",
    "hello",
    "hello",
    "hello",
    'hello <pasted_content id="a',  # the newline after the cut piece is not "the end": the piece stays; the trim drops \n
]


@pytest.mark.parametrize(("raw", "expected"), list(zip(PARITY_CORPUS, PARITY_EXPECTED, strict=True)))
def test_strip_pasted_content_corpus(raw, expected):
    assert v.strip_pasted_content(raw) == expected


def test_strip_pasted_content_preserves_inner_newlines_byte_for_byte():
    raw = '<pasted_content id="a">\r\n  line1\n\n\tline2  \n</pasted_content id="a">'
    assert v.strip_pasted_content(raw) == "line1\n\n\tline2"


def test_strip_pasted_content_empty_string():
    assert v.strip_pasted_content("") == ""


def test_pasted_content_tag_sql_is_the_are_pattern():
    assert v.PASTED_CONTENT_TAG_SQL == (
        r'</?pasted_content(?:[ \t\n\r\f\v]+[A-Za-z0-9_-]+="[^"<>\n]*")*'
        r'(?:>|(?:[ \t\n\r\f\v]+(?:[A-Za-z0-9_-]+(?:=(?:"[^"<> \t\n\r\f\v]*)?)?)?)?$)'
    )
