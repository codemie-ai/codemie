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

MOCK_PROVIDER_DATA = {
    "name": "provider",
    "service_location_url": "http://path1.com",
    "configuration": {"auth_type": "Bearer"},
    "provided_toolkits": [
        {
            "name": "toolkit1",
            "description": "",
            "provided_tools": [
                {
                    "name": "tool1",
                    "description": "",
                    "args_schema": {
                        "param1": {
                            "type": "String",
                            "required": False,
                            "description": "",
                        },
                        "param2": {
                            "type": "Number",
                            "required": True,
                            "description": "",
                        },
                    },
                    "tool_metadata": {},
                    "tool_result_type": "String",
                    "sync_invocation_supported": True,
                    "async_invocation_supported": False,
                }
            ],
            "toolkit_config": {
                "type": "type1",
                "description": "",
                "parameters": {
                    "name": {
                        "description": "",
                        "type": "String",
                        "required": False,
                    }
                },
            },
        },
        {
            "toolkit_id": "867ca03a-66af-4720-b024-4a00f86bd453",
            "name": "CodeAnalysesToolkit",
            "description": "This ToolKit provides tools for indexing the source code of provided repository and set of methods to get insights of the code and code snippets.",
            "toolkit_config": {
                "type": "Code Analyses Datasource Configuration",
                "description": "Configuration for connecting to GitHub repositories to access code, issues, and pull requests",
                "parameters": {
                    "access_token": {
                        "description": "Github/Gitlab project access token with appropriate scopes for repository access",
                        "type": "Secret",
                        "required": True,
                    },
                    "api_url": {
                        "description": "Git API URL (use your Git Enterprise API URL)",
                        "type": "URL",
                        "required": False,
                    },
                    "test_bool": {"description": "Test boolean", "type": "Boolean", "required": False},
                    "datasource_identifier": {
                        "description": "Unique identifier for the datasource",
                        "type": "UUID",
                        "required": False,
                    },
                    "branch": {
                        "description": "Branch to index, defaults to master",
                        "type": "String",
                        "required": False,
                    },
                    "test_number": {"description": "Test number", "type": "Number", "required": False},
                },
            },
            "provided_tools": [
                {
                    "name": "create_datasource",
                    "description": "Creates a new datasource by indexing a Git repository for code analysis. The indexing process extracts code structure, dependencies, and other metadata to enable subsequent queries.",
                    "args_schema": {
                        "analyzer": {
                            "type": "List",
                            "required": False,
                            "description": "Type of code analyzer to use for the project. Supported analyzers Java/TS/JS/C#/C++. If not specified best matching analyzer will be chosen automatically.",
                        },
                        "project_root": {
                            "type": "String",
                            "required": True,
                            "description": "Root directory of the project to be indexed",
                        },
                        "exclude_blob": {
                            "type": "String",
                            "required": False,
                            "description": "Comma separated string of file paths to exclude from indexing",
                        },
                    },
                    "tool_metadata": {
                        "tool_type": "stateful",
                        "tool_purpose": "life_cycle_management",
                        "tool_action_type": "create",
                    },
                    "tool_result_type": "Json",
                    "sync_invocation_supported": False,
                    "async_invocation_supported": True,
                },
                {
                    "name": "reindex_datasource",
                    "description": "Updates an existing datasource by re-indexing the associated Git repository to incorporate the latest changes and maintain synchronization with the current codebase.",
                    "args_schema": {},
                    "tool_metadata": {
                        "tool_type": "stateful",
                        "tool_purpose": "life_cycle_management",
                        "tool_action_type": "modify",
                    },
                    "tool_result_type": "Json",
                    "sync_invocation_supported": False,
                    "async_invocation_supported": True,
                },
                {
                    "name": "delete_datasource",
                    "description": "Removes an existing datasource and all its indexed data from the system, freeing up resources and cleaning up stale references.",
                    "args_schema": {},
                    "tool_metadata": {
                        "tool_type": "stateful",
                        "tool_purpose": "life_cycle_management",
                        "tool_action_type": "remove",
                    },
                    "tool_result_type": "Json",
                    "sync_invocation_supported": False,
                    "async_invocation_supported": True,
                },
                {
                    "name": "get_files_tree_structure",
                    "description": "Retrieves a hierarchical representation of the file system structure of the indexed repository, showing directories and files in a tree format for easy navigation.",
                    "args_schema": {
                        "path": {
                            "type": "String",
                            "required": True,
                            "description": "Relative path within the repository to start the tree structure from",
                        },
                        "level": {
                            "type": "Number",
                            "required": False,
                            "description": "Maximum depth of the tree structure, defaults to full depth",
                        },
                        "limit": {
                            "type": "Number",
                            "required": False,
                            "description": "Maximum number of files to include in the tree structure, defaults to all files",
                        },
                    },
                    "tool_metadata": {
                        "tool_type": "stateful",
                        "tool_purpose": "data_retrieval",
                        "tool_action_type": None,
                    },
                    "tool_result_type": "Json",
                    "sync_invocation_supported": True,
                    "async_invocation_supported": False,
                },
                {
                    "name": "get_code_members",
                    "description": "Extracts and returns structured information about code components such as classes, methods, functions, and variables within specified file in the indexed repository.",
                    "args_schema": {
                        "file_path": {
                            "type": "String",
                            "required": True,
                            "description": "Path of the file to extract code members from",
                        },
                        "start_line": {
                            "type": "Number",
                            "required": False,
                            "description": "Starting line number to extract code members from, defaults to 1",
                        },
                        "end_line": {
                            "type": "Number",
                            "required": False,
                            "description": "Ending line number to extract code members till, defaults to end of file",
                        },
                    },
                    "tool_metadata": {
                        "tool_type": "stateful",
                        "tool_purpose": "data_retrieval",
                        "tool_action_type": None,
                    },
                    "tool_result_type": "Json",
                    "sync_invocation_supported": False,
                    "async_invocation_supported": True,
                },
                {
                    "name": "get_trimmed_code",
                    "description": "Retrieves a simplified version of code from specified files with non-essential elements (like comments and some whitespace) removed to focus on the core functionality.",
                    "args_schema": {
                        "file_path": {
                            "type": "String",
                            "required": True,
                            "description": "Path of the file to extract trimmed code from",
                        },
                        "start_line": {
                            "type": "Number",
                            "required": False,
                            "description": "Starting line number to extract trimmed code from, defaults to 1",
                        },
                        "show_line_numbers": {
                            "type": "Boolean",
                            "required": False,
                            "description": "Flag to include line numbers in the trimmed code, defaults to False",
                        },
                        "end_line": {
                            "type": "Number",
                            "required": False,
                            "description": "Ending line number to extract trimmed code till, defaults to end of file",
                        },
                    },
                    "tool_metadata": {
                        "tool_type": "stateful",
                        "tool_purpose": "data_retrieval",
                        "tool_action_type": None,
                    },
                    "tool_result_type": "String",
                    "sync_invocation_supported": True,
                    "async_invocation_supported": False,
                },
                {
                    "name": "get_code",
                    "description": "Fetches the complete, unmodified source code of specified files or code segments from the indexed repository, preserving all original formatting and comments.",
                    "args_schema": {
                        "file_path": {
                            "type": "String",
                            "required": True,
                            "description": "Path of the file to extract code from",
                        },
                        "start_line": {
                            "type": "Number",
                            "required": False,
                            "description": "Starting line number to extract code from, defaults to 1",
                        },
                        "show_line_numbers": {
                            "type": "Boolean",
                            "required": False,
                            "description": "Flag to include line numbers in the code, defaults to False",
                        },
                        "end_line": {
                            "type": "Number",
                            "required": False,
                            "description": "Ending line number to extract code till, defaults to end of file",
                        },
                    },
                    "tool_metadata": {
                        "tool_type": "stateful",
                        "tool_purpose": "data_retrieval",
                        "tool_action_type": None,
                    },
                    "tool_result_type": "String",
                    "sync_invocation_supported": True,
                    "async_invocation_supported": False,
                },
                {
                    "name": "get_outgoing_dependencies",
                    "description": "Analyzes and returns information about external dependencies and imports used by specified files or code components, helping to understand code relationships and dependency chains.",
                    "args_schema": {
                        "file_path": {
                            "type": "String",
                            "required": True,
                            "description": "Path of the file to get outgoing dependencies from",
                        },
                        "start_line": {
                            "type": "Number",
                            "required": False,
                            "description": "Starting line number to get outgoing dependencies from, defaults to 1",
                        },
                        "end_line": {
                            "type": "Number",
                            "required": False,
                            "description": "Ending line number to get outgoing dependencies till, defaults to end of file",
                        },
                    },
                    "tool_metadata": {
                        "tool_type": "stateful",
                        "tool_purpose": "data_retrieval",
                        "tool_action_type": None,
                    },
                    "tool_result_type": "Json",
                    "sync_invocation_supported": True,
                    "async_invocation_supported": False,
                },
            ],
        },
    ],
}
