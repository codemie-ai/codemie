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

from codemie_tools.base.models import ToolMetadata

CODE_SEARCH_TOOL = ToolMetadata(
    name="search_code_repo",
    description="""
    Tool to search code context for repository in generic approach.
    Repository description: {}.
    You must use this tool anytime, because you need context from repository codebase.
    Useful when you need to have a look on repository code and find relevant context for user input.
    REQUIRED parameters:
    - 'query': is raw user input text query without any modifications;;
    - 'file_path': is a list of relevant file paths from repository tree which might be relevant to user input and used by additional filtration.
    OPTIONAL parameters:
    - 'keywords_list': is a list of keywords values based on user input to filter text in each search result file for get more relevant results.
    """,
    react_description="""
    Tool to search code context for repository in generic approach.
    Repository description: {}.
    You must use this tool anytime, because you need context from repository codebase.
    Useful when you need to have a look on repository code and find relevant context for user input.
    Tool get the following input parameters:
    'query' (Required): is raw user input text query without any modifications;
    'keywords_list': is a list of keywords values based on user input to filter text in each search result file for get more relevant results;
    'file_path' (Required): is a list of relevant file paths from repository tree which might be relevant to user input and used by additional filtration.
    """,
)

CODE_SEARCH_BY_PATHS_TOOL = ToolMetadata(
    name="search_code_repo_by_path",
    description="""
    Searches the repository codebase to retrieve relevant code snippets and files.
    You MUST use this tool when specific file paths and query will be enough to answer user's question.
    E.g. you have all specific file path for question: 'generate summary for /azure module, or so.'
    Repository description: {}.
    Use this tool for ANY code-related questions to ensure accurate responses.
    Input requires the user's raw query and specific file paths to examine.

    REQUIRED parameters:
    - 'query' (user's raw input without modifications);
    - 'file_path' (array of repository paths to search within).
    """,
    react_description="""
    Searches the repository codebase to retrieve relevant code snippets and files.
    You must use this tool when you have specific file paths to search within the repository and don't
    need additional context except the provided file paths.
    Repository description: {}.
    Use this tool for ANY code-related questions to ensure accurate responses.
    Input requires the user's raw query and specific file paths to examine.
    Optional keywords parameter increases result precision by filtering file content.
    Repository context is ESSENTIAL for providing correct technical guidance.

    REQUIRED parameters:
    - 'query' (user's raw input without modifications);
    - 'file_path' (array of repository paths to search within).
    OPTIONAL parameters:
    - 'keywords_list': is a list of keywords values based on user input to filter text in each search result file for get more relevant results;
    """,
)

REPO_TREE_TOOL = ToolMetadata(
    name="get_repository_file_tree",
    description="""
    Useful when you want to get code repository file tree for repository.
    Repository description: {}.
    It must be the first tool to use to get project context. Then, you must uses "search" tool to get advanced context.
    Returns list of 'paths' in the repository.
    You do not need to pass arguments, it will return file tree of current selected repository.
    Parameters:
    'query' (Required): is raw user input text query without any modifications;
    'search_path' (Optional): is a path to the file/module/extension in the repository tree which might
    be relevant to user input and used by additional filtration. 'search_path' is useful when user specified particular module/file/extension,
    e.g. 'find all modules in web module' ('web' is a module name).
    """,
    react_description="""
    Useful when you want to get code repository file tree for repository.
    Repository description: {}.
    It must be the first tool to use to get project context. Then, you must uses "search" tool to get advanced context.
    Returns list of 'paths' in the repository.
    You do not need to pass arguments, it will return file tree
    of current selected repository.
    """,
)

CODE_SEARCH_TOOL_V2 = ToolMetadata(
    name="search_code_repo_v2",
    label="Search Code with filtering (Experimental)",
    description="""
    Tool to search code context for repository in generic approach.
    Repository description: {}.
    You must use this tool to get context from repository codebase.
    You must use this tool only once, unless you are specifically requested to do so by the user.
    Tool Parameters:
    'query' (Required): is detailed query based on user task which will be used to find and filter relevant context;
    'keywords_list': is a list of keywords values based on user input to filter text in each search result file for get more relevant results;
    'file_path' (Required): is a list of relevant file paths from repository tree which might be relevant to user input and used by additional filtration.
    """,
    react_description="""
    Tool to search code context for repository in generic approach.
    Repository description: {}.
    You must use this tool anytime, because you need context from repository codebase.
    Useful when you need to have a look on repository code and find relevant context for user input.
    Tool get the following input parameters:
    'query' is Detailed user input text query which will be used to find and filter relevant context;
    'keywords_list' is a list of keywords values based on user input to filter text in each search result file for get more relevant results;
    'file_path' is a list of file paths from repository tree which might be relevant to user input and used by additional filtration.
    """,
    user_description="""Improved version for Search Code tool for code repository.
    By selecting this tool you override default Search Code tool which is automatically applied to assistant
    when you select Code Datasource.
    There are regular cases when we do not need all populated context from repository codebase,
    thus it can confuse LLM models by adding irrelevant context or result can be truncated due to tokens limitation.
    This tool may be used to only relevant context with smart filtering using LLM.
    Thus, LLM decides which files and documents should be included as a context.
    Note: This is experimental version of Search Code tool.
    """,
)

REPO_TREE_TOOL_V2 = ToolMetadata(
    name="get_repository_file_tree_v2",
    label="Get Repo Tree with filtering (Experimental)",
    description="""
    Useful when you want to get code repository file tree for repository.
    Repository description: {}.
    It must be the first tool to use to get project context if direct file name or path is not specified in query.
    Then, you must uses "search" tool to get advanced context.
    It will return file paths tree of current selected repository.
    Tool get the following input parameters: 'query' is accurate detailed user input or text query
    which will be used to find and filter relevant context.
    Parameters:
    'query' (Required): is raw or detailed user input text query for filtering relevant context;
    'file_path' (Optional): is a path or name to the file/module/extension in the repository tree which might
    be relevant to user input and used by additional filtration.
    Example: 'find all modules in web module' ('web' is 'file_path' field as module name).
    """,
    react_description=REPO_TREE_TOOL.react_description,
    user_description="""
    Useful when you want to get code repository file tree for repository.
    Repository description: {}.
    It must be the first tool to use to get project context if direct file name or path is not specified in query.
    Then, you must uses "search" tool to get advanced context.
    It will return file paths tree of current selected repository.
    Tool get the following input parameters: 'query' is accurate detailed user input or text query
    which will be used to find and filter relevant context.
    Parameters:
    'query' (Required): is raw user input text query without any modifications;
    'search_path' (Optional): is a path to the file/module/extension in the repository tree which might
    be relevant to user input and used by additional filtration. 'search_path' is useful when user specified particular module/file/extension,
    e.g. 'find all modules in web module' ('web' is a module name).
    """,
)

READ_FILES_TOOL = ToolMetadata(
    name="read_files_content",
    label="Read Files Content",
    description="""
    You must use this tool to read the content of a specific file in the repository when the file name/path is known.
    For example, 'Score.java', 'com/example/myfile.txt' or 'com.example.myfile.txt'.
    Useful when you need to read file content directly when you have full file path, full package path or exact file name.
    This tool is ideal for directly accessing file content without performing a broader search.
    Repository description: {}.
    Tool get the following input parameters:
    'file_path' is direct file path to get file content.
    """,
    react_description="""
    You must use this tool to read the content of a specific file in the repository when the file path/package_path is known.
    Useful when you need to read file content directly when you have full file path, full package path or exact file name.
    This tool is ideal for directly accessing file content without performing a broader search.
    Repository description: {}.
    Tool get the following input parameters:
    'file_path' is direct file path to get file content.
    """,
    user_description="""
    The Read Files Content tool allows you to directly access and read the content of a specific file within the code repository,
    provided you know the file path or name.
    This tool is particularly useful when you need detailed information about a particular file without the need for a
    broader search across the repository.
    To use this tool, simply provide the file path of the file you wish to read.
    The tool will return the content of the specified file or files.

    Note: This tool is efficient for pinpointing and retrieving the contents of individual files,
    making it a valuable resource when precise file content is required.
    """,
)

READ_FILES_WITH_SUMMARY_TOOL = ToolMetadata(
    name="read_files_content_summary",
    label="Read Files Content With Summary For Large",
    description="""
    You must use this tool to read the content of a specific file in the repository when the file name/path is known.
    For example, 'Score.java', 'com/example/myfile.txt' or 'com.example.myfile.txt'.
    Useful when you need to read file content directly when you have full file path,
    full package path or exact file name.
    This tool is ideal for directly accessing file content without performing a broader search.
    Repository description: {}.
    Tool get the following input parameters:
    'file_path' is direct file path to get file content;
    'summarization_instructions' Additional details important instructions for summarization of the file content
    provided by user;
    """,
    react_description="""
    You must use this tool to read the content of a specific file in the repository when the
    file path/package_path is known.
    Useful when you need to read file content directly when you have full file path,
    full package path or exact file name.
    This tool is ideal for directly accessing file content without performing a broader search.
    Repository description: {}.
    Tool get the following input parameters:
    'file_path' is direct file path to get file content;
    'summarization_instructions' Additional details important instructions for
    summarization of the file content provided by user;
    """,
    user_description="""
    The Read Files Content With Summary tool allows you to directly access and read the content
    of a specific file within
    the code repository, provided you know the file path or name.
    This enhanced version of the file reader automatically handles large files by providing intelligent summaries when
    the content exceeds token limits.

    Key features:
    - Reads full content for files within token limits
    - Automatically summarizes larger files while preserving key information
    - Focuses summaries on main functionality, purpose, and key components
    - Maintains original file source information

    To use this tool, simply provide the file path of the file you wish to read.
    The tool will return either the complete content or a summarized version, depending on the file size.

    Note: This tool is particularly useful when dealing with large codebases or files, as it ensures you get the most
    relevant information while staying within system limits. If you receive a summary,
    it will be clearly marked as such.
    """,
)
