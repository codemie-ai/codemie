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

import logging
import subprocess
from typing import Tuple, Dict, List

from codemie_tools.code.linter.linter import Linter

logger = logging.getLogger(__name__)


class PythonLinter(Linter):
    error_codes: str

    def __init__(self, error_codes):
        self.error_codes = error_codes

    def lint_code_diff(self, old_content: str, new_content: str) -> Tuple[bool, str]:
        errors = self.lint_single_code(new_content)

        if not errors:
            return True, ""

        changed_lines = self.get_changed_lines(old_content, new_content)
        new_errors = self._filter_new_errors(errors, changed_lines)

        if not new_errors:
            return True, ""

        error_message = self._format_errors(new_errors)
        return False, error_message

    def lint_single_code(self, content: str) -> Dict[int, str]:
        try:
            error_lines = self._run_flake8_cli(content, self.error_codes)
        except subprocess.SubprocessError as e:
            logger.error(f"An error occurred while running flake8: {e}")
            return {}

        error_dict = {}
        for error in error_lines:
            parts = error.split(':')
            if len(parts) > 1:
                try:
                    line_number = int(parts[1])
                    error_dict[line_number] = error
                except ValueError:
                    logger.error(f"Could not parse line number from error: {error}")

        return error_dict

    @staticmethod
    def _run_flake8_cli(content: str, error_codes: str):
        res_cli = subprocess.run(
            ["flake8", "--select", error_codes, "-"], input=content, text=True, capture_output=True, check=False
        )
        return res_cli.stdout.splitlines()

    @staticmethod
    def _filter_new_errors(errors: Dict[int, str], changed_lines: Dict[int, str]) -> List[Tuple[str, str]]:
        retained_errors = []

        for line_number, error_detail in errors.items():
            if line_number in changed_lines:
                line_content = changed_lines[line_number]
                retained_errors.append((line_content, error_detail))

        return retained_errors

    @staticmethod
    def _format_errors(errors: List[Tuple[str, str]]) -> str:
        formatted_errors = []
        for line_content, error_details in errors:
            formatted_errors.append(f"Line: {line_content}\nError: {error_details}\n")
        return "\n".join(formatted_errors)
