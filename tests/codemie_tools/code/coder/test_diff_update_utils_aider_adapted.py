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

from codemie_tools.code.coder.diff_update_coder import (
    strip_quoted_wrapping,
    find_original_update_blocks,
    replace_most_similar_chunk,
)


class TestDiffUpdateUtilsAiderAdapted(unittest.TestCase):
    def test_strip_quoted_wrapping_no_filename(self):
        input_text = "!!!\nWe just want this content\nNot the triple quotes\n!!!"
        expected_output = "We just want this content\nNot the triple quotes\n"
        result = strip_quoted_wrapping(input_text)
        self.assertEqual(result, expected_output)

    def test_strip_quoted_wrapping_no_wrapping(self):
        input_text = "We just want this content\nNot the triple quotes\n"
        expected_output = "We just want this content\nNot the triple quotes\n"
        result = strip_quoted_wrapping(input_text)
        self.assertEqual(result, expected_output)

    def test_find_original_update_blocks(self):
        edit = """
Here's the change:

!!!java
<<<<<<< SEARCH
Two
=======
Tooooo
>>>>>>> REPLACE
!!!

Hope you like it!
"""

        edits = list(find_original_update_blocks(edit))
        self.assertEqual(edits, [("Two\n", "Tooooo\n")])

    def test_find_original_update_blocks_unclosed(self):
        edit = """
Here's the change:

```text
<<<<<<< SEARCH
Two
=======
Tooooo


oops!
"""

        with self.assertRaises(ValueError) as cm:
            list(find_original_update_blocks(edit))
        self.assertIn("Incomplete", str(cm.exception))

    def test_find_original_update_blocks_no_final_newline(self):
        edit = """
<<<<<<< SEARCH
            self.console.print("[red]^C again to quit")
=======
            self.io.tool_error("^C again to quit")
>>>>>>> REPLACE

<<<<<<< SEARCH
            self.io.tool_error("Malformed ORIGINAL/UPDATE blocks, retrying...")
            self.io.tool_error(err)
=======
            self.io.tool_error("Malformed ORIGINAL/UPDATE blocks, retrying...")
            self.io.tool_error(str(err))
>>>>>>> REPLACE

<<<<<<< SEARCH
            self.console.print("[red]Unable to get commit message from gpt-3.5-turbo. Use /commit to try again.\n")
=======
            self.io.tool_error("Unable to get commit message from gpt-3.5-turbo. Use /commit to try again.")
>>>>>>> REPLACE

<<<<<<< SEARCH
            self.console.print("[red]Skipped commmit.")
=======
            self.io.tool_error("Skipped commmit.")
>>>>>>> REPLACE"""

        # Should not raise a ValueError
        list(find_original_update_blocks(edit))

    def test_incomplete_edit_block_missing_filename(self):
        edit = """
No problem! Here are the changes to patch `subprocess.check_output` instead of `subprocess.run` in both tests:

```python
<<<<<<< SEARCH
    def test_check_for_ctags_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("ctags not found")
=======
    def test_check_for_ctags_failure(self):
        with patch("subprocess.check_output") as mock_check_output:
            mock_check_output.side_effect = Exception("ctags not found")
>>>>>>> REPLACE

<<<<<<< SEARCH
    def test_check_for_ctags_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(args=["ctags", "--version"], returncode=0, stdout='''{
  "_type": "tag",
  "name": "status",
  "path": "aider/main.py",
  "pattern": "/^    status = main()$/",
  "kind": "variable"
}''')
=======
    def test_check_for_ctags_success(self):
        with patch("subprocess.check_output") as mock_check_output:
            mock_check_output.return_value = '''{
  "_type": "tag",
  "name": "status",
  "path": "aider/main.py",
  "pattern": "/^    status = main()$/",
  "kind": "variable"
}'''
>>>>>>> REPLACE
```

These changes replace the `subprocess.run` patches with `subprocess.check_output` patches in both `test_check_for_ctags_failure` and `test_check_for_ctags_success` tests.
"""
        edit_blocks = list(find_original_update_blocks(edit))
        self.assertEqual(len(edit_blocks), 2)  # 2 edits

    def test_replace_part_with_missing_varied_leading_whitespace(self):
        whole = """
    line1
    line2
        line3
    line4
"""

        part = "line2\n    line3\n"
        replace = "new_line2\n    new_line3\n"
        expected_output = """
    line1
    new_line2
        new_line3
    line4
"""

        result = replace_most_similar_chunk(whole, part, replace)
        self.assertEqual(result, expected_output)

    def test_replace_part_with_missing_leading_whitespace(self):
        whole = "    line1\n    line2\n    line3\n"
        part = "line1\nline2\n"
        replace = "new_line1\nnew_line2\n"
        expected_output = "    new_line1\n    new_line2\n    line3\n"

        result = replace_most_similar_chunk(whole, part, replace)
        self.assertEqual(result, expected_output)

    def test_replace_part_with_just_some_missing_leading_whitespace(self):
        whole = "    line1\n    line2\n    line3\n"
        part = " line1\n line2\n"
        replace = " new_line1\n     new_line2\n"
        expected_output = "    new_line1\n        new_line2\n    line3\n"

        result = replace_most_similar_chunk(whole, part, replace)
        self.assertEqual(result, expected_output)

    def test_replace_part_with_missing_leading_whitespace_including_blank_line(self):
        """
        The part has leading whitespace on all lines, so should be ignored.
        But it has a *blank* line with no whitespace at all, which was causing a
        bug per issue #25. Test case to repro and confirm fix.
        """
        whole = "    line1\n    line2\n    line3\n"
        part = "\n  line1\n  line2\n"
        replace = "  new_line1\n  new_line2\n"
        expected_output = "    new_line1\n    new_line2\n    line3\n"

        result = replace_most_similar_chunk(whole, part, replace)
        self.assertEqual(result, expected_output)
