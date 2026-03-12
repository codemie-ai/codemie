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

from typing import List, Optional

from pydantic import BaseModel, Field


class GetRepoTreeInput(BaseModel):
    query: str = Field(default="", description="""User initial request should be passed as a string.""")


class GetRepoTreeInputV2(BaseModel):
    query: str = Field(
        description="""Detailed user input text query which will be used to find and filter relevant context.
        It must be detailed, not a short one, because it requires context for searching documents."""
    )
    file_path: Optional[str] = Field(
        description="""Relative path or name to the file/module/extension in the repository tree which might
        be relevant to user input and used by additional filtration.""",
        default=None,
    )


class SearchInput(BaseModel):
    query: str = Field(
        description="""Detailed user query based on user task which will be used to find and filter relevant context.
        It must be detailed, not a short one, because it requires context for searching documents."""
    )
    file_path: Optional[List[str]] = Field(
        description="""List of file paths from repository tree which might be relevant to user input and used by
        additional filtration.""",
        default=[],
    )
    keywords_list: Optional[List[str]] = Field(
        description="""Relevant keywords based on the user query to enhance search results; if no additional
        filtering is needed, return an empty list.""",
        default=[],
    )


class SearchInputByPaths(BaseModel):
    query: str = Field(
        description="""Detailed user query based on user task which will be used to find and filter relevant context.
        It must be detailed, not a short one, because it requires context for searching documents."""
    )
    file_path: Optional[List[str]] = Field(
        description="""List of file paths from repository tree which might be relevant to user input and used by
        additional filtration.""",
        default=[],
    )
    keywords_list: Optional[List[str]] = Field(
        description="""Relevant keywords based on the user query to enhance search results; if no additional
        filtering is needed, return an empty list.""",
        default=[],
    )
    limit_docs_count: Optional[int] = Field(
        description="""Limit returned count of relevant documents to specific number.""",
        default=None,
    )


class ReadFilesInput(BaseModel):
    file_path: str = Field(
        description="""Real file name or file path to the file to be read. Must get exact from user input."""
    )


class ReadFilesWithSummaryInput(BaseModel):
    file_path: str = Field(
        description="""Real file name or file path to the file to be read. Must get exact from user input."""
    )
    summarization_instructions: str = Field(
        description="""Additional details important instructions for summarization of the file content
provided by user."""
    )


class FilteredDocuments(BaseModel):
    sources: List[str] = Field(
        description="""
        List of only relevant sources according to user task.
        It must be full source paths value without any changes like it was provided.
        """.strip(),
    )
