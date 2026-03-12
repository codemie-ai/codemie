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

import unittest

from langchain_core.messages import AIMessage

from codemie_tools.code.coder.diff_update_coder import (
    get_edits,
    try_dotdotdots,
    apply_edits,
    update_content_by_task,
    get_lang_from_response,
    extract_and_apply_edits,
    pretty_format_edits,
)


class TestDiffUpdateUtils(unittest.TestCase):
    def test_get_edits_success(self):
        raw_changes = """
Here's the change:

```text
foo.txt
<<<<<<< SEARCH
Two
=======
Tooooo
>>>>>>> REPLACE
```

Hope you like it!
"""
        edits = get_edits(raw_changes)
        assert edits == [("Two\n", "Tooooo\n")]

    def test_get_edits_not_ends_with_separator(self):
        raw_changes = """
Here's the change:

```text
foo.txt
<<<<<<< SEARCH
Two
=======
Tooooo
>>>>>>> REPLACE
```

Hope you like it!"""
        edits = get_edits(raw_changes)
        assert edits == [("Two\n", "Tooooo\n")]

    def test_get_edits_error(self):
        raw_changes = """
Here's the change:

```text
foo.txt
<<<<<<< SEARCH
Two
=======
Tooooo
>>>>>>> REPLACE
```

Hope you like it!"""
        edits = get_edits(raw_changes)
        assert edits == [("Two\n", "Tooooo\n")]

    def test_no_dots(self):
        whole = "This is some content."
        part = "This is some content."
        replace = "This is some new content."
        result = try_dotdotdots(whole, part, replace)
        self.assertIsNone(result)

    def test_mismatched_dots(self):
        whole = "This is some content."
        part = "This is some content.\n...\nMore content."
        replace = "This is some new content.\n...\nOther content."
        with self.assertRaises(ValueError):
            try_dotdotdots(whole, part, replace)

    def test_perfect_edit(self):
        whole = "This is some content.\nMore content."
        part = "This is some content.\n...\nMore content."
        replace = "This is some new content.\n...\nOther content."
        result = try_dotdotdots(whole, part, replace)
        expected = "This is some new content.\nOther content."
        self.assertEqual(result, expected)

    def test_append_content(self):
        whole = "This is some content.\nMore content."
        part = "...\nMore content."
        replace = "...\nOther content."
        result = try_dotdotdots(whole, part, replace)
        expected = "This is some content.\nOther content."
        self.assertEqual(result, expected)

    def test_apply_edits_success(self):
        content = """
This is some content.
Two
More content.
"""
        edits = [("Two\n", "Tooooo\n")]
        result = apply_edits(edits, content)
        expected = """
This is some content.
Tooooo
More content.
"""
        self.assertEqual(result, expected)

    def test_apply_edits_failure(self):
        content = """
This is some content.
Three
More content.
"""
        edits = [("Two\n", "Tooooo\n")]
        with self.assertRaises(ValueError):
            apply_edits(edits, content)

    def test_update_content_by_task_with_retry(self):
        responses = [
            AIMessage(content="No changes. Corrupted LLM response"),
            AIMessage(
                content="""
Here's the change:
```text
<<<<<<< SEARCH
Test content
=======
New file content
>>>>>>> REPLACE
```

Hope you like it!
"""
            ),
        ]
        llm = MockLLM(responses)
        old_content = """
Test content
"""
        new_content, _ = update_content_by_task(old_content, "Some task description", llm)

        self.assertEqual(
            new_content,
            """
New file content
""",
        )

    def test_get_lang_from_response(self):
        llm_resp_python = """
Here are the *SEARCH/REPLACE* blocks:
!!!python
<<<<<<< SEARCH
=======
def hello():
    "print a greeting"

    print("hello")
>>>>>>> REPLACE
!!!
"""
        lang = get_lang_from_response(llm_resp_python)
        self.assertEqual(lang, "python")

    def test_get_lang_from_response_not_found(self):
        llm_resp_python = """
Here are the *SEARCH/REPLACE* blocks:
!!!
<<<<<<< SEARCH
=======
package main

import "fmt"

func main() {
    fmt.Println("Hello, World!")
}
!!!
"""
        lang = get_lang_from_response(llm_resp_python)
        self.assertEqual(lang, "")

    old_code_valid = """def foo():
    return 42
"""

    new_code_valid = """def foo():
    return 42

def bar():
    return 42
"""

    def test_extract_and_apply_python_valid_code(self):
        llm_resp_python = f"""
Here are the *SEARCH/REPLACE* blocks:
!!!python
<<<<<<< SEARCH
{self.old_code_valid}
=======
{self.new_code_valid}
>>>>>>> REPLACE
!!!
"""
        new_code, _ = extract_and_apply_edits(llm_resp_python, self.old_code_valid)
        self.assertEqual(new_code, self.new_code_valid)

    new_code_indent_issue = """
def foo():
    return 42

def bar():
return 42
"""

    def test_extract_and_apply_python_invalid_code(self):
        llm_resp_python = f"""
Here are the *SEARCH/REPLACE* blocks:
!!!python
<<<<<<< SEARCH
{self.old_code_valid}
=======
{self.new_code_indent_issue}
>>>>>>> REPLACE
!!!
"""
        with self.assertRaises(ValueError) as context:
            extract_and_apply_edits(llm_resp_python, self.old_code_valid)
        self.assertIn("E999", str(context.exception))

    def test_pretty_format_edits(self):
        example_list = [
            ("apple", "fruit"),
            ("carrot", "vegetable"),
        ]
        res_str = pretty_format_edits(example_list)
        expected_str = """Change 1:
Original Code:
apple

New Code:
fruit
----------------------------------------
Change 2:
Original Code:
carrot

New Code:
vegetable
----------------------------------------"""
        self.assertEqual(res_str, expected_str)

    def test_apply_edits_not_unique_lines(self):
        changes = [("apple\n", "fruit\n")]
        original_content = """
apple
apple
"""
        with self.assertRaises(ValueError) as context:
            apply_edits(changes, original_content)
        self.assertIn("not unique in the original content", str(context.exception))


class MockLLM:
    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0
        self.temperature = 0.7
        self.top_p = 0.2

    def invoke(self, messages):
        response = self.responses[self.call_count]
        self.call_count += 1
        return response
