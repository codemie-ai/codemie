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
from langchain_core.documents import Document
from pydantic import BaseModel, Field

from codemie.core.models import CreatedByUser
from codemie.datasource.loader.platform.platform_loader import BasePlatformLoader
from codemie.rest_api.models.assistant import Assistant
from codemie.configs import logger


class SafeTool(BaseModel):
    """
    Sanitized tool details for marketplace indexing (no credentials).
    """

    name: str
    label: Optional[str] = None
    user_description: Optional[str] = None


class SafeToolKit(BaseModel):
    """
    Sanitized toolkit details for marketplace indexing (no credentials).
    """

    toolkit: str
    tools: list[SafeTool] = Field(default_factory=list)
    label: str = ""
    is_external: Optional[bool] = False


class AssistantDocument(BaseModel):
    """
    Whitelist model for assistants in marketplace datasource.
    """

    id: str
    name: str
    slug: Optional[str] = None
    description: str
    project: str
    system_prompt: str
    categories: Optional[list[str]] = Field(default_factory=list)
    # Toolkits (sanitized - no credentials, used in page_content)
    toolkits: list[SafeToolKit] = Field(default_factory=list)
    # Creator info (used in page_content)
    created_by: Optional[CreatedByUser] = None
    # Usage statistics (used in page_content and popularity_score calculation)
    unique_users_count: Optional[int] = 0
    unique_likes_count: Optional[int] = 0
    unique_dislikes_count: Optional[int] = 0


class AssistantLoaderFilters(BaseModel):
    """
    Filters for loading assistants from database.

    These filters are used by AssistantLoader to query assistants from PostgreSQL
    before indexing them into the platform datasource.

    Attributes:
        is_global: Filter by publication status (True=published, False=private, None=all)
        project: Filter by project name
        categories: Filter by category IDs
    """

    is_global: bool | None = True
    project: str | None = None
    categories: list[str] | None = None


class AssistantLoader(BasePlatformLoader):
    """Load published assistants from PostgreSQL for indexing into marketplace datasource."""

    ASSISTANT_PROMPT_MAX_LENGTH = 1000
    POPULARITY_SCORE_SCALING_FACTOR = 10000  # Tuned for 100-8200 users range

    def __init__(self, filters: Optional[AssistantLoaderFilters] = None):
        """
        Initialize AssistantLoader with optional filters.

        Args:
            filters: Filters to apply when fetching assistants.
                     If None, defaults to AssistantLoaderFilters() (only published assistants).
        """
        super().__init__()
        self.filters = filters or AssistantLoaderFilters()

    def _fetch_entities(self) -> List[Assistant]:
        """Fetch assistants from database based on configured filters."""
        filter_dict = self.filters.model_dump(exclude_none=True)

        logger.info("Fetching assistants from database", extra={"filters": filter_dict})

        # Get assistants matching filter criteria
        assistants = Assistant.get_all_by_fields(filter_dict)

        logger.info(f"Found {len(assistants)} assistants", extra={"count": len(assistants), "filters": filter_dict})
        return assistants

    def _sanitize_entity(self, assistant: Assistant) -> AssistantDocument:
        """
        Convert assistant to marketplace-safe format using whitelist approach.
        Args:
            assistant: Assistant object to sanitize

        Returns:
            AssistantDocument with only safe fields
        """
        # Pydantic automatically filters out fields not defined in AssistantDocument
        return AssistantDocument(**assistant.model_dump())

    def _extract_toolkits_info(self, toolkits: List[dict]) -> tuple[List[str], List[str]]:
        """
        Extract toolkit and tool names from toolkits configuration.

        Args:
            toolkits: List of toolkit configurations

        Returns:
            Tuple of (toolkit_names, tool_names)
        """
        toolkit_names = []
        tool_names = []

        for tk in toolkits:
            toolkit_name = tk.get('toolkit', '')
            if toolkit_name:
                toolkit_names.append(toolkit_name)

            tk_tool_names = [tool.get('name') for tool in tk.get('tools', []) if tool.get('name')]
            tool_names.extend(tk_tool_names)

        return toolkit_names, tool_names

    def _format_usage_stats(self, unique_users: int, likes: int, dislikes: int) -> str | None:
        """
        Format usage statistics into a human-readable string.

        Args:
            unique_users: Number of unique users
            likes: Number of likes
            dislikes: Number of dislikes

        Returns:
            Formatted usage string or None if no stats available
        """
        if not any((unique_users, likes, dislikes)):
            return None

        usage_info = []
        if unique_users > 0:
            usage_info.append(f"{unique_users} users")
        if likes > 0:
            usage_info.append(f"{likes} likes")
        if dislikes > 0:
            usage_info.append(f"{dislikes} dislikes")

        return f"Usage: {', '.join(usage_info)}"

    def _calculate_popularity_score(self, unique_users: int, likes: int, dislikes: int) -> float:
        """
        Calculate normalized popularity score using arctan normalization.

        Formula combines two factors:
        1. Usage volume: arctan(users / scaling_factor) - base score from user count
        2. Feedback balance: (likes - dislikes) multiplier when feedback exists

        When no feedback: score based purely on user count
        When feedback exists: user count weighted by feedback balance

        Real-world examples (current platform data: 100-8200 users):
        - 8200 users, 0 likes, 0 dislikes: → ~0.69 (popular by usage)
        - 8200 users, 200 likes, 20 dislikes: → ~0.998 (top rated)
        - 100 users, 0 likes, 0 dislikes: → ~0.51 (some usage)
        - 100 users, 30 likes, 5 dislikes: → ~0.578 (above average)
        - 100 users, 10 likes, 10 dislikes: → ~0.51 (neutral feedback)
        - 100 users, 5 likes, 30 dislikes: → ~0.44 (negative feedback)

        Args:
            unique_users: Number of unique users
            likes: Number of likes
            dislikes: Number of dislikes

        Returns:
            Popularity score in range [0, 1]
        """
        import math

        if unique_users == 0:
            return 0.0

        total_feedback = likes + dislikes

        if total_feedback == 0:
            # No feedback yet - score based purely on usage volume
            # arctan(users / scaling_factor) gives smooth growth
            raw_score = unique_users
        else:
            # Has feedback - combine usage with feedback balance
            # Positive feedback multiplies, negative reduces
            feedback_multiplier = (likes - dislikes) / total_feedback
            raw_score = unique_users * (1 + feedback_multiplier)

        # Apply arctan normalization using class constant
        normalized = math.atan(raw_score / self.POPULARITY_SCORE_SCALING_FACTOR)

        # Rescale from (-π/2, π/2) to (0, 1)
        score = (normalized + math.pi / 2) / math.pi

        return round(score, 4)

    def _add_toolkits(self, content_parts: list[str], sanitized: AssistantDocument) -> None:
        """Add toolkits and tools to content parts if present."""
        if not sanitized.toolkits:
            return

        # Convert to dict format for compatibility with existing method
        toolkits_dict = [tk.model_dump() for tk in sanitized.toolkits]
        toolkit_names, tool_names = self._extract_toolkits_info(toolkits_dict)

        if toolkit_names:
            content_parts.append(f"Toolkits: {', '.join(toolkit_names)}")

        if tool_names:
            content_parts.append(f"Tools: {', '.join(tool_names)}")

    def _add_creator(self, content_parts: list[str], sanitized: AssistantDocument) -> None:
        """Add creator information to content parts if present."""
        creator_name = (
            sanitized.created_by.username if sanitized.created_by and sanitized.created_by.username else 'System'
        )
        content_parts.append(f"Created by: {creator_name or 'System'}")

    def _add_usage_stats(self, content_parts: list[str], sanitized: AssistantDocument) -> None:
        """Add usage statistics to content parts if present."""
        unique_users = sanitized.unique_users_count or 0
        likes = sanitized.unique_likes_count or 0
        dislikes = sanitized.unique_dislikes_count or 0

        usage_text = self._format_usage_stats(unique_users, likes, dislikes)
        if usage_text:
            content_parts.append(usage_text)

    def _build_page_content(self, sanitized: AssistantDocument) -> str:
        """
        Build page content for document embeddings.

        Args:
            sanitized: Sanitized assistant data

        Returns:
            Formatted page content string
        """
        content_parts = [
            f"Name: {sanitized.name}",
            f"Page url with details: /#/assistants/{sanitized.id}",
            f"Project: {sanitized.project}",
            f"Description: {sanitized.description}",
            f"System Prompt: {sanitized.system_prompt[: self.ASSISTANT_PROMPT_MAX_LENGTH]}",
        ]

        if sanitized.slug:
            content_parts.insert(1, f"Slug: {sanitized.slug}")

        if sanitized.categories:
            content_parts.append(f"Categories: {', '.join(sanitized.categories)}")

        self._add_toolkits(content_parts, sanitized)
        self._add_creator(content_parts, sanitized)
        self._add_usage_stats(content_parts, sanitized)

        return "\n\n".join(content_parts)

    def _build_metadata(self, sanitized: AssistantDocument) -> dict:
        """
        Build minimal metadata dictionary for document.

        Args:
            sanitized: Sanitized assistant data

        Returns:
            Minimal metadata with id (for Elasticsearch), name, source, popularity_score, and analytics fields
        """
        unique_users = sanitized.unique_users_count or 0
        likes = sanitized.unique_likes_count or 0
        dislikes = sanitized.unique_dislikes_count or 0

        popularity_score = self._calculate_popularity_score(unique_users, likes, dislikes)

        return {
            'id': sanitized.id,  # Required for Elasticsearch document ID
            'name': sanitized.name,  # Assistant name for display and processed_files
            'source': sanitized.name,  # Required by RRF for exact match field (using name as unique identifier)
            'popularity_score': popularity_score,  # Normalized score for ranking
        }

    def _entity_to_document(self, assistant: Assistant) -> Document:
        """
        Convert Assistant to LangChain Document for indexing.

        The document structure:
        - page_content: Text for embeddings (name + description + system_prompt + usage stats)
        - metadata: All sanitized fields for filtering and retrieval

        Args:
            assistant: Assistant to convert

        Returns:
            LangChain Document ready for indexing
        """
        sanitized = self._sanitize_entity(assistant)
        page_content = self._build_page_content(sanitized)
        metadata = self._build_metadata(sanitized)

        return Document(page_content=page_content, metadata=metadata)

    def load_single_entity(self, entity_id: str) -> Optional[Document]:
        """
        Load a single assistant by ID and convert to document.
        Used for incremental indexing on publish/unpublish.

        Args:
            entity_id: ID of the assistant to load

        Returns:
            LangChain Document or None if assistant not found or not published
        """
        logger.info(f"Loading single assistant: {entity_id}", extra={"assistant_id": entity_id})

        # Find assistant by ID
        assistant = Assistant.find_by_id(entity_id)

        if not assistant:
            logger.warning(f"Assistant {entity_id} not found", extra={"assistant_id": entity_id})
            return None

        # Check if assistant is published
        if not assistant.is_global:
            logger.warning(
                f"Assistant {entity_id} is not published (is_global=False)",
                extra={"assistant_id": entity_id, "is_global": False},
            )
            return None

        # Convert to document
        document = self._entity_to_document(assistant)

        logger.info(
            f"Successfully loaded assistant {entity_id}",
            extra={"assistant_id": entity_id, "assistant_name": assistant.name},
        )

        return document
