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
from typing import Tuple

from codemie_tools.code.linter.impl.python import PythonLinter

logger = logging.getLogger(__name__)


class LinterFacade:
    PYTHON_LINTER_ERROR_CODES: str = "E999,F821"

    def __init__(self):
        self.linters = {"python": PythonLinter(error_codes=self.PYTHON_LINTER_ERROR_CODES)}

    def lint_code(self, lang: str, old_content: str, content_candidate: str) -> Tuple[bool, str]:
        linter = self.linters.get(lang)
        if not linter:
            logger.info(f"Unsupported language: {lang}")
            return True, ""
        return linter.lint_code_diff(old_content, content_candidate)
