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


prompt = """
Your main goal is to select relevant sections from the provided list to sources and the user input.
Given the following question and list of sources, select all the sources that are possibly relevant to the question.
If there are not any, return an empty list.
You MUST select only relevant sources from provided list (including summary if provided) for user ask.
Be careful and correct.

### Sources:
{sources}

### User question:
{question}
"""


KB_SOURCES_SELECTOR_PROMPT = PromptTemplate.from_template(prompt)
