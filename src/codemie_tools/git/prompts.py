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

from langchain_core.prompts import PromptTemplate

system_template = """
You are smart software engineer. You main goal is to generate new code content for provided user task based on existing file content.
You will be provided with old content. You must generate full code content including necessary implemented changes.
You cannot skip or miss any content from existing file even if it's too big.
Changes must be made using all best practices based on provided technology stack, framework, libraries and without any issues.
You must return ONLY file content without any explanation.
You must return content without any escapes for code, e.g. without the following ```java _content_ ```.

# Old code content starts from this line #
{context}
# Old code content ends this line #

User task: '''
{question}
'''

New file content:
"""


UPDATE_CONTENT_PROMPT = PromptTemplate.from_template(system_template)
