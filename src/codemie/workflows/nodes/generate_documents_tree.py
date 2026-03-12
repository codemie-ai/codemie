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

import re
from typing import Any, Dict, List, Optional

from codemie.configs import logger
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.utils import check_file_type
from codemie.core.workflow_models import CustomWorkflowNode
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.base_node import BaseNode
from codemie.workflows.utils import get_documents_tree_by_datasource_id
from pydantic import BaseModel, Field


def _filter_by_regex(documents_tree: List[Dict[str, str]], pattern: str) -> List[Dict[str, str]]:
    """Filter documents using a regex pattern."""
    logger.info(f"Filtering documents tree by regex pattern. Pattern: {pattern}")
    try:
        regex_pattern = re.compile(pattern)
        return [doc for doc in documents_tree if regex_pattern.search(doc.get('file_path', doc.get('source', '')))]
    except Exception as exc:
        logger.error(f"Error during filtering by regex pattern: {pattern}: {exc}")
        return documents_tree


def _filter_by_file_type(documents_tree: List[Dict[str, str]], pattern: str) -> List[Dict[str, str]]:
    """Filter documents using a file type pattern."""
    logger.info(f"Filtering documents tree by file type pattern. Pattern: {pattern}")
    try:
        return [
            doc
            for doc in documents_tree
            if check_file_type(
                file_name=doc.get('file_path', doc.get('source', '')),
                files_filter=pattern,
                repo_local_path='',  # Update this according to your repo_local_path context
                excluded_files=[],  # Update this according to your excluded files context
            )
        ]
    except Exception as exc:
        logger.error(f"Error during filtering by file type pattern: {pattern}: {exc}")
        return documents_tree


def _filter_documents_tree(
    documents_tree: List[Dict[str, str]], documents_filtering_pattern: str = None, documents_filter: str = None
) -> List[Dict[str, str]]:
    """
    Filters a list of documents based on a file pattern.
    :param documents_tree: List of dictionaries to be filtered.
    :param documents_filtering_pattern: A glob-like pattern that the file_path must match for a dictionary to be
     included in the result.
    :param documents_filter: A new pattern that the file_path must match for a dictionary to be included
     in the result.
    :return: A new list of dictionaries filtered based on the file_path matching the file pattern.
    """
    original_size = len(documents_tree)

    if documents_filtering_pattern:
        filtered_documents_tree = _filter_by_regex(documents_tree, documents_filtering_pattern)
    elif documents_filter:
        filtered_documents_tree = _filter_by_file_type(documents_tree, documents_filter)
    else:
        filtered_documents_tree = documents_tree

    logger.info(
        f"Filtered documents tree. OriginalSize: {original_size}. FilteredTreeSize: {len(filtered_documents_tree)}"
    )
    return filtered_documents_tree


class GenerateDocumentsTreeConfigSchema(BaseModel):
    """Configuration schema for GenerateDocumentsTree."""

    datasource_id: str = Field(
        ...,
        json_schema_extra={
            "type": "str",
            "required": True,
            "description": "ID of the datasource to generate documents from",
        },
    )
    documents_filtering_pattern: Optional[str] = Field(
        None,
        json_schema_extra={
            "type": "str",
            "required": False,
            "description": "Pattern to filter documents (optional)",
        },
    )
    documents_filter: Optional[str] = Field(
        None,
        json_schema_extra={
            "type": "str",
            "required": False,
            "description": "Filter for documents (optional)",
        },
    )
    output_key: Optional[str] = Field(
        None,
        json_schema_extra={
            "type": "str",
            "required": False,
            "description": "Key for output (default: 'documents_tree')",
        },
    )
    include_content: Optional[bool] = Field(
        None,
        json_schema_extra={
            "type": "bool",
            "required": False,
            "description": "Include document content (default: False)",
        },
    )


class GenerateDocumentsTree(BaseNode[AgentMessages]):
    config_schema = GenerateDocumentsTreeConfigSchema

    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        *args,
        **kwargs,
    ):
        super().__init__(callbacks, workflow_execution_service, thought_queue, *args, **kwargs)

    def execute(self, state_schema: AgentMessages, execution_context: dict) -> Any:
        custom_node: CustomWorkflowNode = execution_context.get("custom_node")
        datasource_id = custom_node.config.get('datasource_id')
        documents_filtering_pattern = custom_node.config.get('documents_filtering_pattern')
        documents_filter = custom_node.config.get('documents_filter')
        output_key = custom_node.config.get('output_key') or 'documents_tree'
        include_content = custom_node.config.get('include_content', False)

        logger.info(
            f"Execute {custom_node.id}. Started. "
            f"DatasourceId={datasource_id}. "
            f"DocumentsFilteringPattern={documents_filtering_pattern}. "
            f"DocumentsFilterPattern={documents_filter}. "
            f"WithText={include_content}"
        )
        filtered_documents_tree = _filter_documents_tree(
            documents_tree=get_documents_tree_by_datasource_id(datasource_id, include_content=include_content),
            # Implement logic to filter on elastic level and configurable in gitignore manner
            documents_filtering_pattern=documents_filtering_pattern,
            documents_filter=documents_filter,
        )
        return {output_key: filtered_documents_tree}

    def get_task(self, state_schema: AgentMessages, *arg, **kwargs):
        return "List documents from datasource"
