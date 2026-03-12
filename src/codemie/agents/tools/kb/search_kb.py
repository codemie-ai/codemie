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

from typing import Type, List, Optional

from pydantic import BaseModel, Field
from langchain_core.documents import Document

from codemie_tools.base.constants import SOURCE_DOCUMENT_KEY, SOURCE_FIELD_KEY, FILE_CONTENT_FIELD_KEY
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.models import ToolMetadata
from codemie.agents.utils import adapt_tool_name
from codemie.configs import logger
from codemie.core.constants import REQUEST_ID
from codemie.core.dependecies import get_llm_by_credentials
from codemie.datasource.google_doc.google_doc_datasource_processor import GoogleDocDatasourceProcessor
from codemie.rest_api.models.index import IndexInfo, IndexInfoType
from codemie.service.search_and_rerank import SearchAndRerankKB
from codemie.service.search_and_rerank.marketplace import SearchAndRerankMarketplace
from codemie.service.constants import FullDatasourceTypes
from codemie.templates.knowledge_base_prompt import LLM_ROUTING_KB_PROMPT


SEARCH_KB_TOOL = ToolMetadata(
    name="search_kb",
    description="""Use this tool to retrieve or search for additional project context needed to resolve a user's query.
It accepts the following input parameter:
  - query: A string containing the detailed user query, which will be used to locate relevant context.
""",
)


class SearchInput(BaseModel):
    query: str = Field(
        description="String text. It's raw detailed user input text query which will be used to find relevant context."
    )


class LLMRouting(BaseModel):
    sections: List[str] = Field(
        description="List of relevant sections numbers from the knowledge base to return",
    )


class SearchKBTool(CodeMieTool):
    truncate_message: str = (
        "The query provided to this tool is overly broad, which resulted in a truncated output. "
        "**Please ask the user to narrow down their query or provide more specific details about what they need.** "
        "A more focused question will enable a more accurate and complete response. "
        "Bellow is the truncated output:\n"
    )

    kb_index: Optional[IndexInfo] = None
    llm_model: Optional[str] = None
    base_name: str = "search_kb"
    name_template: str = base_name + "_{}"
    tokens_size_limit: int = Field(default_factory=lambda: 20000)
    description_template: str = """
    Use this tool when you need to get or search additional project context to resolve user query.
    Tool get the following input parameters: "query": string text with detailed user query which will be used to
    find relevant context.
    Tool knowledge description: {}.
    """
    name: str = name_template.format("default")
    description: str = description_template.format("default")
    args_schema: Type[BaseModel] = SearchInput

    def __init__(self, kb_index: IndexInfo, llm_model: str):
        super().__init__()
        self.kb_index = kb_index
        self.llm_model = llm_model
        self.name = adapt_tool_name(self.name_template, kb_index.repo_name)
        self.description = self.description_template.format(kb_index.description)

    def execute(self, query: str, **kwargs):
        if self.kb_index and ("llm_routing" in self.kb_index.index_type):
            return self.process_llm_routing_index(query=query, kb_index=self.kb_index)

        if self.kb_index and (self.kb_index.index_type == IndexInfoType.KB_BEDROCK.value):
            return self.process_knowledge_base_bedrock_index(query=query, kb_index=self.kb_index)
        else:
            request_id = self.metadata.get(REQUEST_ID)

            if self.kb_index.index_type == FullDatasourceTypes.PLATFORM_ASSISTANT.value:
                search_class = SearchAndRerankMarketplace
            else:
                search_class = SearchAndRerankKB

            data = search_class(
                query=query,
                kb_index=self.kb_index,
                llm_model=self.llm_model,
                top_k=10,  # TODO: make it configurable
                request_id=request_id,
            ).execute()

        return self.format_response(data)

    def format_document(self, doc):
        source = doc.metadata['source']
        chunk_num = f"-{doc.metadata['chunk_num']}" if 'chunk_num' in doc.metadata else ""
        source_field = f"{source}{chunk_num}"

        return (
            f"\n{SOURCE_DOCUMENT_KEY}\n"
            f"{SOURCE_FIELD_KEY}{source_field}\n"
            f"{FILE_CONTENT_FIELD_KEY} \n{doc.page_content}\n"
        )

    def format_response(self, documents: List[Document]):
        try:
            return "\n".join(self.format_document(doc) for doc in documents)
        except Exception as e:
            logger.error(f"Error while formatting response: {e}")
            return documents

    def process_llm_routing_index(self, query: str, kb_index):
        request_id = self.metadata.get(REQUEST_ID)
        llm = get_llm_by_credentials(llm_model=self.llm_model, request_id=request_id)
        processor = GoogleDocDatasourceProcessor(
            datasource_name=kb_index.repo_name,
            project_name=kb_index.project_name,
            google_doc=kb_index.google_doc_link,
        )
        sections = "\n".join(processor.get_table_of_contents())
        search_chain = LLM_ROUTING_KB_PROMPT | llm.with_structured_output(LLMRouting)

        selected_sections = search_chain.invoke({"sections": str(sections), "input": query})
        logger.debug(f"Selected sections for KB: {selected_sections}")

        selected_docs = processor.get_documents_by_checksum(selected_sections.sections)
        documents = list(selected_docs.values())

        final_response = "\n".join(
            [
                f"\n{SOURCE_DOCUMENT_KEY}\n"
                f"{SOURCE_FIELD_KEY}{doc['title']}\n"
                f"{FILE_CONTENT_FIELD_KEY} \n{doc['content']}\n"
                for doc in documents
            ]
        )

        return final_response

    def process_knowledge_base_bedrock_index(self, query: str, kb_index: IndexInfo):
        # Import here to avoid circular imports
        from codemie.service.aws_bedrock.bedrock_knowledge_base_service import BedrockKnowledgeBaseService

        if not kb_index.id:
            logger.error("Knowledge base index ID is not set.")
            return []

        response = BedrockKnowledgeBaseService.invoke_knowledge_base(
            query=query,
            bedrock_index_info_id=kb_index.id,
        )

        formatted_docs = []
        for i, item in enumerate(response):
            page_content = item.get("content", {}).get("text", "")
            metadata = item.get("metadata", {})
            location = item.get("location", {})

            # determine a "source" reliably
            source = (
                metadata.get("x-amz-bedrock-kb-source-uri")
                or metadata.get("source")
                or metadata.get("x-amz-bedrock-kb-data-source-id")
                or metadata.get("x-amz-bedrock-kb-chunk-id")
                or location.get("s3Location", {}).get("uri")
                or location.get("webLocation", {}).get("url")
                or location.get("kendraDocumentLocation", {}).get("url")
                or f"{kb_index.repo_name}-bedrock-doc-{i}"
            )

            formatted_docs.append(
                f"\n{SOURCE_DOCUMENT_KEY}\n{SOURCE_FIELD_KEY}{source}\n{FILE_CONTENT_FIELD_KEY} \n{page_content}\n"
            )

        return "\n".join(formatted_docs)
