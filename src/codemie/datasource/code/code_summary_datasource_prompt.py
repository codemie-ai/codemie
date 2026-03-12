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

file_summary = """
You are acting as a code documentation expert for a project.
Below is the code from a file that has the name '{fileName}'.
Focus on the high-level purpose of the code and how it may be used in the larger project.
Include code examples where appropriate.
DO NOT RETURN LONG AND HUGE DESCRIPTION.
Do not just list the methods and classes in this file.
If the file is build tool file, list all available dependencies and libs, e.g. pom.xml, requirements.txt, etc.

Output should be in the following markdown format:
# {fileName}
## Overview
Briefly introduce the file (or Java class, python, etc.), outlining its primary purpose and functionality. Explain the role it plays in a software project.
## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Usage](#usage)
3. [Methods](#methods)
4. [Useful details](#properties)
## Prerequisites
List any dependencies or prerequisites required to use the file. Include version information if applicable.
## Usage
Provide clear instructions on how to instantiate and utilize it in a project.
## Methods
Provide description of methods and functions in file (including their parameters, values, like JAVADOC).
## Useful details
Provide any additional details that may be helpful to the reader.

Here is the code of file:
{fileContents}

Response:
"""

FILE_SUMMARY_PROMPT = PromptTemplate.from_template(file_summary)

CUSTOM_SUMMARY_TEMPLATE_SUFFIX = """
Below is the code from a file that has the name '{fileName}'.
Here is the code of file:
{fileContents}

Response:
"""

readme_template = """
You are a Developer with Years of Experience
Your task is to write all the documentation of the project you are developing
Focus on the high-level purpose of the code and how it may be used in the larger project.
You will have to read all the context that makes up the project and write an introduction
to the purpose of the project followed by a definition of the structure of the code to
which you will attach the purpose of that specific component
Readme file do not exist, you MUST create it based on given context

Output should be in the following markdown format:
# {fileName}

## Overview
Briefly introduce the folder outlining its primary purpose and functionality. Explain the role it plays in a software project.
## Usage
Provide clear instructions on how to instantiate and utilize it in a project.
## Useful details
Provide any additional details that may be helpful to the reader.

Here is the context for readme:
{fileContents}
"""
README_GEN_PROMPT = PromptTemplate.from_template(readme_template)

summarized_chunk_content = """
CODE:
{code}
__________________________________________
SUMMARIZATION:
{summarization}
"""


prompt = """
You are acting as a code documentation expert for a project.
Below is the code from a file that has the name '{fileName}'.
Write a detailed technical explanation of what this code does.
Focus on the low-level purpose of the code.
DO NOT RETURN MORE THAN 100 WORDS.
Do not just list the methods and classes in this file.

code:
{fileContents}

Response:
"""

CHUNK_SUMMARY_PROMPT = PromptTemplate.from_template(prompt)
