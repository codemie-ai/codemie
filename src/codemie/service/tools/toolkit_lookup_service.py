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

import uuid
from typing import List, Optional

from codemie_tools.base.models import Tool, ToolKit
from langchain_core.documents import Document
from langchain_elasticsearch import ElasticsearchStore

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.dependecies import get_elasticsearch
from codemie.core.models import AssistantChatRequest
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.search_and_rerank.tool import SearchAndRerankTool


class ToolkitLookupService:
    """Service for indexing and searching tools using semantic search.

    This service is responsible for:
    - Indexing all available tools into Elasticsearch
    - Building context-aware search queries from user requests and chat history
    - Performing semantic search to find relevant tools
    - Reconstructing ToolKit objects from search results
    """

    @classmethod
    def index_all_tools(cls) -> int:
        """Index all available tools into Elasticsearch.

        This method clears the existing index and creates new records for all tools.
        Should be called on application startup.

        Returns:
            Number of tools indexed
        """
        logger.info("Starting tool indexing (ToolSearch)")

        # Get metadata and prepare documents
        tools_metadata = cls._get_tools_metadata()
        documents = cls._prepare_tool_documents(tools_metadata)

        # Set up indexing configuration and index documents
        return cls._index_documents(documents)

    @classmethod
    def _get_tools_metadata(cls) -> List[dict]:
        """Retrieve all tools metadata from the ToolsInfoService.

        Returns:
            List of toolkit metadata dictionaries
        """
        from codemie.service.tools.tools_info_service import ToolsInfoService

        tools_metadata = ToolsInfoService.get_tools_info(show_for_ui=True)
        logger.info(f"Retrieved metadata for {len(tools_metadata)} toolkits")
        return tools_metadata

    @classmethod
    def _index_documents(cls, documents: List[Document]) -> int:
        """Index the given documents in Elasticsearch.

        Args:
            documents: The documents to index

        Returns:
            Number of documents indexed

        Raises:
            Exception: If indexing fails
        """
        # Set up indexing configuration
        index_name, store = cls._setup_elasticsearch_index()

        # Index all documents
        try:
            logger.debug(f"Starting batch indexing. DocumentCount={len(documents)}, Index={index_name}")
            store.add_documents(
                documents=documents,
                create_index_if_not_exists=False,
                refresh_indices=False,
                bulk_kwargs={
                    "max_retries": 3,
                },
            )
            logger.info(f"Successfully indexed {len(documents)} tools in Elasticsearch. Index={index_name}")
            return len(documents)
        except Exception as e:
            logger.error(
                f"Failed to index tools. Index={index_name}, DocumentCount={len(documents)}, Error={e}", exc_info=True
            )
            raise

    @classmethod
    def _prepare_tool_documents(cls, tools_metadata: List[dict]) -> List[Document]:
        """Prepare document objects for indexing from tools metadata.

        Args:
            tools_metadata: List of toolkit metadata dictionaries

        Returns:
            List of Document objects ready for indexing
        """
        documents = []

        for toolkit_meta in tools_metadata:
            toolkit_name = toolkit_meta.get("toolkit", "unknown")
            tools = toolkit_meta.get("tools", [])

            # Extract toolkit-level metadata
            toolkit_level_meta = cls._extract_toolkit_metadata(toolkit_meta)

            logger.debug(
                f"Processing toolkit: {toolkit_name}, ToolCount={len(tools)}, ToolkitMeta={toolkit_level_meta}"
            )

            for tool_meta in tools:
                doc = cls._create_tool_document(tool_meta, toolkit_name, toolkit_level_meta)
                documents.append(doc)

        logger.info(f"Prepared {len(documents)} tools for indexing")
        return documents

    @classmethod
    def _extract_toolkit_metadata(cls, toolkit_meta: dict) -> dict:
        """Extract and namespace toolkit metadata fields.

        Args:
            toolkit_meta: Raw toolkit metadata dictionary

        Returns:
            Dictionary with properly namespaced toolkit metadata
        """
        # Namespace toolkit fields to avoid conflicts with tool fields
        # Keep 'toolkit' field unprefixed for RRF deduplication and text search
        toolkit_level_meta = {}
        for k, v in toolkit_meta.items():
            if k == "tools":
                # Tools are indexed separately
                continue
            elif k == "toolkit":
                # Keep toolkit name unprefixed for RRF and search compatibility
                toolkit_level_meta[k] = v
            else:
                # Prefix other toolkit fields to avoid conflicts
                toolkit_level_meta[f"toolkit_{k}"] = v

        return toolkit_level_meta

    @classmethod
    def _create_tool_document(cls, tool_meta: dict, toolkit_name: str, toolkit_level_meta: dict) -> Document:
        """Create a Document object for a single tool.

        Args:
            tool_meta: Tool metadata dictionary
            toolkit_name: Name of the toolkit this tool belongs to
            toolkit_level_meta: Namespaced toolkit metadata

        Returns:
            Document object ready for indexing
        """
        logger.debug(f"Processing tool: {tool_meta}")
        tool_name = tool_meta.get("name", "")
        tool_description = tool_meta.get("description", "") or tool_meta.get("user_description", "")
        if not tool_description:
            tool_description = ""

        # Tokenize tool name for better search matching
        name_tokens = SearchAndRerankTool.tokenize_tool_name(tool_name)
        name_tokens_str = " ".join(name_tokens)

        # Build enhanced content for semantic search
        content = f"{tool_name}\n{name_tokens_str}\n{tool_description}"

        # Store complete metadata for toolkit reconstruction
        doc_metadata = {
            **tool_meta,  # Include all tool-level metadata (unchanged)
            **toolkit_level_meta,  # Include all toolkit-level metadata (prefixed)
            "name_tokens": name_tokens_str,  # Add tokenized name for search
        }

        doc = Document(page_content=content, metadata=doc_metadata, id=str(uuid.uuid4()))

        logger.debug(
            f"Prepared tool. Toolkit={toolkit_name}, Tool={tool_name}, "
            f"ToolId={doc.id}, Tokens={name_tokens}, "
            f"DescriptionLength={len(tool_description)}, MetadataFields={list(doc_metadata.keys())}"
        )

        return doc

    @classmethod
    def _setup_elasticsearch_index(cls) -> tuple[str, ElasticsearchStore]:
        """Set up Elasticsearch index and vector store.

        Returns:
            Tuple containing (index_name, elasticsearch_store)
        """
        # Get index name and Elasticsearch store
        index_name = getattr(config, 'TOOLS_INDEX_NAME', 'codemie_tools')
        default_embedding_model = llm_service.default_embedding_model
        embedding_deployment_name = llm_service.get_embedding_deployment_name(default_embedding_model)

        logger.info(
            f"Initializing Elasticsearch store. Index={index_name}, "
            f"EmbeddingModel={default_embedding_model}, "
            f"DeploymentName={embedding_deployment_name}"
        )

        # Delete existing index to force fresh indexing
        try:
            elastic_client = ElasticSearchClient.get_client()
            if elastic_client.indices.exists(index=index_name):
                elastic_client.indices.delete(index=index_name)
                logger.info(f"Deleted existing Elasticsearch index: {index_name}")
        except Exception as e:
            logger.warning(f"Failed to delete existing index (may not exist): {e}")

        # Get Elasticsearch store
        try:
            store = get_elasticsearch(index_name, embedding_deployment_name)
            logger.info(f"Initialized Elasticsearch vector store. Index={index_name}")
            return index_name, store
        except Exception as e:
            logger.error(f"Failed to initialize Elasticsearch store: {e}", exc_info=True)
            raise

    @classmethod
    def _reconstruct_toolkit_from_metadata(cls, doc_metadata: dict) -> ToolKit:
        """Reconstruct ToolKit object with single Tool from indexed document metadata.

        This method separates toolkit-prefixed fields from tool fields and creates
        Pydantic ToolKit and Tool objects from the metadata.

        Args:
            doc_metadata: Document metadata from Elasticsearch containing both
                         toolkit fields (prefixed with "toolkit_" except 'toolkit' name) and tool fields

        Returns:
            ToolKit object containing a single Tool (the one that was found by search)
        """
        toolkit_meta = {}
        tool_meta = {}

        for key, value in doc_metadata.items():
            if key.startswith("toolkit_"):
                # Remove "toolkit_" prefix to restore original field name
                original_key = key[len("toolkit_") :]
                toolkit_meta[original_key] = value
            elif key == "toolkit":
                # Toolkit name is unprefixed for RRF compatibility
                toolkit_meta[key] = value
            elif key == "name_tokens":
                # Skip search-specific field (not part of original metadata)
                continue
            else:
                # Regular tool field
                tool_meta[key] = value

        # Create Tool object from tool metadata
        tool = Tool(**tool_meta)

        # Create ToolKit object with the single tool
        # Note: toolkit_meta already has "toolkit" field, just add the tool
        toolkit_meta["tools"] = [tool]
        return ToolKit(**toolkit_meta)

    @classmethod
    def get_tools_by_query(
        cls,
        query: str,
        limit: int = 5,
        tool_names_filter: Optional[List[str]] = None,
    ) -> List[ToolKit]:
        """Get relevant tools by search query as ToolKit objects.

        Uses SearchAndRerankTool to perform hybrid search with reranking, then
        reconstructs ToolKit and Tool Pydantic objects from indexed documents.

        Tools are grouped by toolkit to avoid duplicates. Each ToolKit may contain
        one or more Tools that matched the search query. This structure preserves
        both toolkit-level and tool-level metadata.

        Args:
            query: Search query text (e.g., "search for code", "list git branches")
            limit: Maximum number of tools to retrieve (default: 5)
            tool_names_filter: Optional list of tool names to filter results.
                               If provided, only tools with names in this list will be returned.

        Returns:
            List of ToolKit objects, each containing one or more Tools from search results.
            Tools from the same toolkit are grouped together in a single ToolKit object.
        """
        try:
            index_name = getattr(config, 'TOOLS_INDEX_NAME', 'codemie_tools')

            logger.debug(
                f"Starting tool search. Query='{query[:100]}...', Limit={limit}, "
                f"Index={index_name}, ToolNamesFilter={len(tool_names_filter) if tool_names_filter else 'None'}"
            )

            # Use SearchAndRerankTool for hybrid search
            search_and_rerank = SearchAndRerankTool(
                query=query,
                top_k=limit,
                index_name=index_name,
                tool_names_filter=tool_names_filter,
            )

            # Execute search and get reranked documents
            reranked_docs = search_and_rerank.execute()

            # Group tools by toolkit to avoid duplicates
            # Key: toolkit name, Value: dict with toolkit metadata and list of tools
            toolkit_groups = {}

            for idx, doc in enumerate(reranked_docs):
                doc_metadata = doc.metadata

                try:
                    # Use _reconstruct_toolkit_from_metadata to parse document
                    single_tool_toolkit = cls._reconstruct_toolkit_from_metadata(doc_metadata)

                    toolkit_name = single_tool_toolkit.toolkit
                    tool = single_tool_toolkit.tools[0]  # Will always have exactly one tool
                    tool_name = tool.name

                    # Group by toolkit name
                    if toolkit_name not in toolkit_groups:
                        # Store toolkit metadata (without tools) for grouping
                        toolkit_groups[toolkit_name] = {
                            "metadata": {
                                "toolkit": single_tool_toolkit.toolkit,
                                **{
                                    k: v
                                    for k, v in single_tool_toolkit.model_dump().items()
                                    if k not in ["toolkit", "tools"]
                                },
                            },
                            "tools": [],
                        }

                    # Add tool to toolkit group
                    toolkit_groups[toolkit_name]["tools"].append(tool)

                    logger.debug(
                        f"Selected tool #{idx + 1}. Toolkit={toolkit_name}, Tool={tool_name}, ToolLabel={tool.label}"
                    )

                except Exception as reconstruction_error:
                    logger.error(
                        f"Failed to process tool metadata: {reconstruction_error}. Metadata={doc_metadata}",
                        exc_info=True,
                    )

            # Reconstruct ToolKit objects from grouped data
            selected_toolkits = []
            for toolkit_name, group_data in toolkit_groups.items():
                try:
                    toolkit_meta = group_data["metadata"]
                    toolkit_meta["tools"] = group_data["tools"]

                    toolkit = ToolKit(**toolkit_meta)
                    selected_toolkits.append(toolkit)

                    logger.debug(f"Reconstructed toolkit: {toolkit_name} with {len(group_data['tools'])} tool(s)")
                except Exception as reconstruction_error:
                    logger.error(
                        f"Failed to reconstruct toolkit '{toolkit_name}': {reconstruction_error}", exc_info=True
                    )

            logger.info(
                f"Tool search complete. Query='{query[:50]}...', "
                f"FoundToolkits={len(selected_toolkits)}, "
                f"TotalTools={sum(len(tk.tools) for tk in selected_toolkits)}, "
                f"Limit={limit}"
            )

            return selected_toolkits

        except Exception as e:
            logger.error(f"Failed to get tools by query: {e}", exc_info=True)
            return []

    @classmethod
    def build_search_query_with_history(cls, request: AssistantChatRequest) -> Optional[str]:
        """Build a context-aware search query using current request text and recent chat history.

        This method combines the current user request with the last 5 messages from chat history
        to provide better context for semantic tool search. Recent conversation context helps
        identify relevant tools based on the ongoing discussion.

        Args:
            request: The assistant chat request containing text and history

        Returns:
            Combined query string with current text and recent history, or None if no text available
        """
        if not request or not hasattr(request, 'text'):
            return None

        current_text = request.text
        if not current_text:
            return None

        # Start with current request text
        query_parts = [current_text]

        # Add history messages if available
        history_messages = cls._extract_history_messages(request)
        query_parts.extend(history_messages)

        # Combine all parts with newlines
        combined_query = "\n".join(query_parts)

        logger.debug(f"ToolSearch: Query length={len(combined_query)} chars, parts={len(query_parts)}")

        return combined_query

    @classmethod
    def _extract_history_messages(cls, request: AssistantChatRequest) -> List[str]:
        """Extract recent message content from chat history.

        Args:
            request: The assistant chat request containing history

        Returns:
            List of message strings from recent history
        """
        message_texts = []

        if not hasattr(request, 'history') or not request.history:
            return message_texts

        try:
            # Handle both list and string history formats
            history_messages = request.history if isinstance(request.history, list) else []

            # Get last 5 messages (most recent context)
            recent_messages = history_messages[-5:] if len(history_messages) > 5 else history_messages

            for msg in recent_messages:
                # Extract message content based on type
                if hasattr(msg, 'message') and msg.message:
                    message_texts.append(msg.message)
                elif isinstance(msg, dict) and 'message' in msg:
                    message_texts.append(msg['message'])

            logger.debug(f"ToolSearch: Extracted {len(message_texts)} messages from history")
        except Exception as e:
            logger.warning(f"ToolSearch: Failed to extract history for query: {e}")

        return message_texts
