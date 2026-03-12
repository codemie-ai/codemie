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

REPO_TREE_FILTER_RELEVANCE_TEMPLATE = """
You are expert in software development, all popular programming languages and professional developer.
Your main goal is to choose relevant sources paths from the provided list of SOURCES according to the user TASK.
You must filter out completely irrelevant sources to avoid confusion and reduce the amount of information to process.
Include only relevant sources paths in the final list.

### SOURCES ###
{sources}
### END SOURCES ###

### TASK ###
{query}
### END TASK ###

You MUST return ONLY list of relevant sources in VALID JSON format.
{{
    "sources": [
        "path/to/source1",
        "path/to/source2",
        ...
    ]
}}

Filtered sources:'''
""".strip()

REPO_TREE_FILTER_RELEVANCE_PROMPT = PromptTemplate.from_template(REPO_TREE_FILTER_RELEVANCE_TEMPLATE)

CODE_FILTER_RELEVANCE_TEMPLATE = """
You are expert in software development, programming languages and professional developer.
Your primary objective is to identify and select the relevant documents from the provided list of DOCUMENTS.
You must analyze user input, query and codebase provided in each document to determine their relevance.
You MUST then provide only the sources of these relevant documents, based on the initial
user input and partial search query. If there is any limit documents count provided,
you must reduce count of most relevant documents to this limit number.
You must include all relevant sources with their chunk_num in the final list.
The idea is to filter out completely irrelevant documents to avoid confusion and reduce
the amount of information to process.

### DOCUMENTS ###
{documents}
### END DOCUMENTS ###

### INITIAL USER INPUT ###
{input}
### END INITIAL USER INPUT ###

### PARTIAL SEARCH QUERY ###
{query}
{keywords_list}
### END PARTIAL SEARCH QUERY ###

### LIMIT DOCUMENTS COUNT ###
{limit_docs_count}

You MUST return ONLY list of sources with chunk_num.
""".strip()

CODE_FILTER_RELEVANCE_PROMPT = PromptTemplate.from_template(CODE_FILTER_RELEVANCE_TEMPLATE)

CODE_SUMMARY_TEMPLATE = """
You are expert in software engineering, programming languages and professional developer.
Your main aim is to summarize the provided code document to make it smaller, because files are very large.

### Important:
1. You MUST NOT miss/avoid any important details from provided code.
2. Generated summary MUST be smaller than the original document, accurately represent the original code.
3. You MUST analyze code and provide summary, don't duplicate/provide the same code again in output.
4. Don't safe tokens, you MUST provide comprehensive and detailed summary.
5. You must get into your context summaries previous chunks for the same file, it must be helpful. BUT,
you MUST NOT duplicate summary content from previous chunks in the new summary.

### PROVIDED CODE ###
{code}
### END PROVIDED CODE ###

### IMPORTANT ADDITIONAL INSTRUCTIONS FOR SUMMARIZATION ###
{task}
### END IMPORTANT ADDITIONAL INSTRUCTIONS FOR SUMMARIZATION ###

### PREVIOUS CHUNKS OF SUMMARIES ###
{previous_summaries}
### END PREVIOUS CHUNKS OF SUMMARIES ###

Result:
""".strip()

CODE_SUMMARY_PROMPT = PromptTemplate.from_template(CODE_SUMMARY_TEMPLATE)

FINAL_SUMMARY_TEMPLATE = """
You are expert in software engineering, programming languages and professional developer.
Your main aim is to summarize the provided chunks of summary documentation to make entire
detailed document with summary, because files are very large.

### Important:
1. You MUST NOT miss/avoid any important details from provided code.
2. Generated summary MUST be smaller than the original document, accurately represent the original code.
3. Don't safe tokens, you MUST provide comprehensive and detailed summary.

### PROVIDED SUMMARIES ###
{summaries}
### END PROVIDED SUMMARIES ###

### IMPORTANT ADDITIONAL INSTRUCTIONS FOR SUMMARIZATION ###
{task}
### END IMPORTANT ADDITIONAL INSTRUCTIONS FOR SUMMARIZATION ###

Result:
""".strip()

FINAL_SUMMARY_PROMPT = PromptTemplate.from_template(FINAL_SUMMARY_TEMPLATE)
