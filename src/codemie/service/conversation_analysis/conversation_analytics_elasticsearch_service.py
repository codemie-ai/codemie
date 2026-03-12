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

"""Elasticsearch service for conversation analytics indexing and querying."""

from __future__ import annotations

from typing import Any


from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import logger
from codemie.rest_api.models.conversation_analysis import ConversationAnalytics

INDEX_NAME = "conversation_analytics"


class ConversationAnalyticsElasticsearchService:
    """Service for managing conversation analytics in Elasticsearch.

    Provides index creation, document indexing, and optimization for Kibana dashboards.
    """

    @staticmethod
    def create_index_if_not_exists() -> None:
        """Create the conversation_analytics index with basic settings.

        Uses dynamic mapping - Elasticsearch will auto-detect field types
        when documents are indexed.
        """
        client = ElasticSearchClient.get_client()

        if client.indices.exists(index=INDEX_NAME):
            logger.info(f"Elasticsearch index '{INDEX_NAME}' already exists")
            return

        logger.info(f"Creating Elasticsearch index '{INDEX_NAME}' with dynamic mapping")
        client.indices.create(
            index=INDEX_NAME,
            body={"settings": {"number_of_shards": 1, "number_of_replicas": 1}},
            ignore=400,
        )
        logger.info(f"Elasticsearch index '{INDEX_NAME}' created successfully")

    @staticmethod
    def transform_to_document(analytics: ConversationAnalytics) -> dict[str, Any]:
        """
        Transform ConversationAnalytics model into optimized Elasticsearch document.

        Optimizations:
        - Flatten assistant data into arrays for aggregations
        - Extract nested values for easier filtering
        - Add computed fields for analytics
        """
        # Convert Pydantic models to dicts
        assistants_data = [a.model_dump() for a in analytics.assistants_used]
        topics_data = [t.model_dump() for t in analytics.topics]
        anti_patterns_data = [ap.model_dump() for ap in analytics.anti_patterns]

        # Extract flattened assistant fields for aggregations (across multiple assistants)
        assistant_ids = [a["id"] for a in assistants_data]
        assistant_names = [a["name"] for a in assistants_data]
        assistant_categories = []
        assistant_tools = []
        for a in assistants_data:
            if a.get("categories"):
                assistant_categories.extend(a["categories"])
            if a.get("tool_names"):
                assistant_tools.extend(a["tool_names"])

        # Extract other_category suggestions from topics
        other_categories = [t.get("other_category") for t in topics_data if t.get("other_category")]

        # Extract anti-pattern codes and severities for aggregations
        anti_pattern_codes = [ap["pattern"] for ap in anti_patterns_data if "pattern" in ap]
        anti_pattern_severities = [ap["severity"] for ap in anti_patterns_data if "severity" in ap]

        doc = {
            # Core fields
            "id": str(analytics.id),
            "conversation_id": analytics.conversation_id,
            "user_id": analytics.user_id,
            "user_name": analytics.user_name,
            "project": analytics.project,  # Project field
            # Timestamps - convert to ISO format strings
            "date": analytics.date.isoformat() if analytics.date else None,
            "update_date": analytics.update_date.isoformat() if analytics.update_date else None,
            "last_analysis_date": analytics.last_analysis_date.isoformat() if analytics.last_analysis_date else None,
            "analyzed_at": analytics.analyzed_at.isoformat() if analytics.analyzed_at else None,
            # Metrics
            "message_count_at_analysis": analytics.message_count_at_analysis,
            "analysis_duration_seconds": analytics.analysis_duration_seconds,
            "llm_model_used": analytics.llm_model_used,
            # Assistants - nested + flattened arrays for aggregations
            "assistants_used": assistants_data,
            "assistant_ids": assistant_ids,
            "assistant_names": assistant_names,
            "assistant_categories": list(set(assistant_categories)),  # Unique categories across all assistants
            "assistant_tools": list(set(assistant_tools)),  # Unique tools across all assistants
            # Topics - keep nested structure + add flattened fields
            "topics": topics_data,
            "topic_categories": list({t["category"] for t in topics_data}) if topics_data else [],
            "usage_intents": list({t["usage_intent"] for t in topics_data}) if topics_data else [],
            "other_categories": other_categories,  # Suggested new categories
            # Satisfaction - nested only
            "satisfaction": analytics.satisfaction.model_dump() if analytics.satisfaction else None,
            # Maturity - nested + level for easy filtering
            "maturity": analytics.maturity.model_dump() if analytics.maturity else None,
            "maturity_level": analytics.maturity.level if analytics.maturity else None,
            # Anti-patterns - nested + flattened patterns and counts for aggregations
            "anti_patterns": anti_patterns_data,
            "anti_patterns_count": len(anti_patterns_data),
            "anti_pattern_codes": anti_pattern_codes,
            "anti_pattern_severities": anti_pattern_severities,
        }

        return doc

    @staticmethod
    def index_analytics(analytics: ConversationAnalytics) -> None:
        """
        Index a conversation analytics document into Elasticsearch.

        Args:
            analytics: ConversationAnalytics instance to index
        """
        client = ElasticSearchClient.get_client()

        try:
            doc = ConversationAnalyticsElasticsearchService.transform_to_document(analytics)
            client.index(index=INDEX_NAME, id=doc["id"], document=doc)
            logger.debug(f"Indexed conversation analytics: {doc['id']} (conversation: {analytics.conversation_id})")
        except Exception as e:
            logger.error(f"Failed to index conversation analytics {analytics.id} to Elasticsearch: {e}", exc_info=True)
            # Don't raise - Elasticsearch indexing is non-critical, PostgreSQL is source of truth

    @staticmethod
    def delete_analytics(analytics_id: str) -> None:
        """
        Delete a conversation analytics document from Elasticsearch.

        Args:
            analytics_id: ID of the analytics record to delete
        """
        client = ElasticSearchClient.get_client()

        try:
            client.delete(index=INDEX_NAME, id=str(analytics_id), ignore=[404])
            logger.debug(f"Deleted conversation analytics from Elasticsearch: {analytics_id}")
        except Exception as e:
            logger.error(f"Failed to delete conversation analytics {analytics_id} from Elasticsearch: {e}")
            # Don't raise - Elasticsearch deletion is non-critical
