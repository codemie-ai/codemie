# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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


from langchain_core.documents import Document
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from codemie_tools.base.constants import SOURCE_DOCUMENT_KEY, SOURCE_FIELD_KEY, FILE_CONTENT_FIELD_KEY
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.models import ToolMetadata
from codemie.agents.callbacks.agent_invoke_callback import AgentInvokeCallback
from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.agents.utils import adapt_tool_name
from codemie.agents.tools.datasource_health_mixin import DatasourceHealthMixin
from codemie.configs import logger
from codemie.core.constants import REQUEST_ID
from codemie.core.dependecies import get_llm_by_credentials
from codemie.datasource.google_doc.google_doc_datasource_processor import GoogleDocDatasourceProcessor
from codemie.datasource.loader.models import is_image_document_metadata
from codemie.rest_api.models.index import IndexInfo, IndexInfoType
from codemie.service.file_service.file_service import FileService
from codemie.service.search_and_rerank import SearchAndRerankKB
from codemie.service.search_and_rerank.marketplace import SearchAndRerankMarketplace
from codemie.service.constants import FullDatasourceTypes
from codemie.templates.knowledge_base_prompt import LLM_ROUTING_KB_PROMPT

_SUPPRESSED_CALLBACK_TYPES = AgentStreamingCallback | AgentInvokeCallback

COVERAGE_KEY = "**Coverage:** "
INCOMPLETE_NOTICE = (
    "###NOTICE###\n"
    "Some parts of the documents listed above are not included in this result. "
    "To obtain a missing part, call this tool again with a narrower query naming the "
    "section or topic you need. Do not supply the missing content from your own knowledge."
)
UNVERIFIED_NOTICE = (
    "###NOTICE###\n"
    "Coverage of the documents listed above could not be verified, so parts may be missing. "
    "To obtain a specific section, call this tool again with a narrower query naming it. "
    "Do not supply missing content from your own knowledge."
)
TRUNCATION_COVERAGE_SUFFIX = " — output was truncated from the end, so parts counted as included may be missing"
OVERLIMIT_NOTICE = (
    "###NOTICE###\n"
    "Some documents above exceed the full-retrieval limit, so no single result can include "
    "all of their parts. A narrower query returns the parts most relevant to it, but repeated "
    "queries are not guaranteed to reconstruct such a document in full. "
    "Do not supply missing content from your own knowledge."
)


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
    sections: list[str] = Field(
        description="List of relevant sections numbers from the knowledge base to return",
    )


class SearchKBResponse(BaseModel):
    """Carries KB text response alongside image artifacts for multimodal delivery."""

    text: str
    image_artifacts: list[dict]

    def __str__(self) -> str:
        return self.text


class SearchKBTool(CodeMieTool, DatasourceHealthMixin):
    truncate_message: str = (
        "The query provided to this tool is overly broad, which resulted in a truncated output. "
        "**Please ask the user to narrow down their query or provide more specific details about what they need.** "
        "A more focused question will enable a more accurate and complete response. "
        "Bellow is the truncated output:\n"
    )

    response_format: str = "content_and_artifact"
    index_info: IndexInfo | None = None
    llm_model: str | None = None
    base_name: str = "search_kb"
    name_template: str = base_name + "_{}"
    tokens_size_limit: int = Field(default_factory=lambda: 20000)
    # Indexed chunk count per source, filled from the search. A source missing from this
    # map is reported as unverified rather than silently assumed complete.
    source_chunk_totals: dict = Field(default_factory=dict)
    # The search path's ceiling for retrieving a document in full (chunk count above which
    # a source is excluded from document routing). Coverage wording depends on it: advising
    # a narrower retry is honest only for sources that can actually be retrieved in full.
    full_retrieval_chunk_limit: int | None = None
    description_template: str = """
    Use this tool when you need to get or search additional project context to resolve user query.
    Tool get the following input parameters: "query": string text with detailed user query which will be used to
    find relevant context.
    Tool knowledge description: {}.
    """
    name: str = name_template.format("default")
    description: str = description_template.format("default")
    args_schema: type[BaseModel] = SearchInput

    def __init__(self, index_info: IndexInfo, llm_model: str):
        super().__init__()
        self.index_info = index_info
        self.llm_model = llm_model
        self.name = adapt_tool_name(self.name_template, index_info.repo_name)
        self.description = self._build_description_health_prefix(index_info) + self.description_template.format(
            index_info.description
        )

    def execute(self, query: str, **kwargs) -> SearchKBResponse:
        if self.index_info and self.index_info.error:
            raise ToolException(self._build_health_notice())
        notice = self._build_health_notice()

        # The two branches below are deliberately outside the coverage contract that
        # format_response() applies to chunk-based retrieval: llm_routing returns an answer
        # synthesized by an LLM over whole documents and KB_BEDROCK returns Bedrock's own
        # retrieval text — neither carries per-source chunk counts a coverage claim could
        # honestly be computed from.
        if self.index_info and ("llm_routing" in self.index_info.index_type):
            text = self.process_llm_routing_index(query=query, kb_index=self.index_info)
            return SearchKBResponse(text=str(self._wrap_result(str(text), notice)), image_artifacts=[])

        if self.index_info and (self.index_info.index_type == IndexInfoType.KB_BEDROCK.value):
            text = self.process_knowledge_base_bedrock_index(query=query, kb_index=self.index_info)
            return SearchKBResponse(text=str(self._wrap_result(str(text), notice)), image_artifacts=[])
        else:
            request_id = self.metadata.get(REQUEST_ID)

            if self.index_info.index_type == FullDatasourceTypes.PLATFORM_ASSISTANT.value:
                search_class = SearchAndRerankMarketplace
            else:
                search_class = SearchAndRerankKB

            search = search_class(
                query=query,
                kb_index=self.index_info,
                llm_model=self.llm_model,
                top_k=10,  # TODO: make it configurable
                request_id=request_id,
            )
            data = search.execute()
            totals = getattr(search, "source_chunk_totals", None)
            # Only a real mapping is usable; anything else means the search path did not
            # supply totals, and a coverage claim must not be invented from it.
            self.source_chunk_totals = totals if isinstance(totals, dict) else {}
            limit = getattr(search, "MAX_CHUNKS_FOR_SINGLE_DOCUMENT", None)
            self.full_retrieval_chunk_limit = limit if isinstance(limit, int) else None

        return SearchKBResponse(
            text=str(self._wrap_result(self.format_response(data), notice)),
            image_artifacts=self._collect_image_artifacts(data),
        )

    def _collect_image_artifacts(self, data: list[Document] | tuple[list[Document], list[str]]) -> list[dict]:
        docs = data[0] if isinstance(data, tuple) else data
        artifacts: list[dict] = []
        for doc in docs:
            if not isinstance(doc, Document):
                continue
            meta = doc.metadata
            if not is_image_document_metadata(meta):
                continue
            try:
                b64 = FileService.get_image_base64(meta["image_encoded_url"])
            except Exception as e:
                logger.warning(f"Failed to fetch image artifact for url={meta['image_encoded_url']!r}: {e}")
                continue
            artifacts.append({"data": b64, "mime_type": meta["image_mime_type"]})
        logger.debug(f"search_kb: retrieved {len(docs)} docs, {len(artifacts)} image artifacts")
        return artifacts

    def _limit_output_content(self, output: SearchKBResponse) -> tuple[SearchKBResponse, int]:
        """Token-limit only the text portion; image artifacts are not plain text."""
        limited_text, token_count = super()._limit_output_content(output.text)
        text = limited_text if isinstance(limited_text, str) else str(limited_text)
        if token_count > self.tokens_size_limit:
            # Truncation cuts from the tail while the coverage lines sit at the head, so an
            # untouched "N of N parts included" claim would survive the cut and be false.
            text = self._mark_coverage_truncated(text)
        return SearchKBResponse(text=text, image_artifacts=output.image_artifacts), token_count

    @staticmethod
    def _mark_coverage_truncated(text: str) -> str:
        # Matched by containment, not prefix: the base truncation wrapper glues its own
        # message onto the first line of the text, so the first coverage line may not
        # start the line it sits on.
        return "\n".join(
            line + TRUNCATION_COVERAGE_SUFFIX if COVERAGE_KEY in line else line for line in text.split("\n")
        )

    def _post_process_output_content(self, output: SearchKBResponse, *args, **kwargs) -> tuple[str, list[dict]]:
        """Return a ``(content, artifact)`` tuple consumed by ``_image_artifact_pre_model_hook``."""
        text = super()._post_process_output_content(output.text, *args, **kwargs)
        return text, output.image_artifacts

    def format_document(self, doc: Document) -> str:
        source = doc.metadata.get('source', '')
        chunk_num = f"-{doc.metadata['chunk_num']}" if 'chunk_num' in doc.metadata else ""
        source_field = f"{source}{chunk_num}"

        return (
            f"\n{SOURCE_DOCUMENT_KEY}\n"
            f"{SOURCE_FIELD_KEY}{source_field}\n"
            f"{FILE_CONTENT_FIELD_KEY} \n{doc.page_content}\n"
        )

    def format_response(self, documents: list[Document] | tuple[list[Document], list[str]]) -> str:
        docs = documents[0] if isinstance(documents, tuple) else documents
        prefix = str(documents[1]) + "\n" if isinstance(documents, tuple) else ""

        shown: dict[str, int] = {}
        for doc in docs:
            source = doc.metadata.get("source", "")
            shown[source] = shown.get(source, 0) + 1

        coverage_lines = []
        flags: set[str] = set()
        for source, count in shown.items():
            line, flag = self._coverage_line(source, count)
            coverage_lines.append(line)
            if flag:
                flags.add(flag)
        coverage_lines.extend(self._coverage_notices(flags))

        body = "\n".join(self.format_document(doc) for doc in docs)
        head = prefix + "\n".join(coverage_lines) if coverage_lines else prefix.rstrip("\n")
        return "\n".join(part for part in (head, body) if part)

    def _coverage_line(self, source: str, count: int) -> tuple[str, str]:
        """Build one source's coverage line and name the flag it raises ('' when none)."""
        total = self.source_chunk_totals.get(source)
        limit = self.full_retrieval_chunk_limit
        if not isinstance(total, int) or total <= 0:
            # Saying nothing would leave a partial result shaped exactly like a complete
            # one, which is the confusion this coverage statement exists to remove.
            return f"{COVERAGE_KEY}{source}: {count} parts included, total not verified", "unverified"
        if isinstance(limit, int) and total > limit:
            # This source is excluded from full-document routing, so the standard
            # "retry with a narrower query" advice cannot obtain its missing parts.
            line = (
                f"{COVERAGE_KEY}{source}: {count} of {total} parts included "
                f"(document exceeds the {limit}-part full-retrieval limit)"
            )
            return line, "over_limit" if count < total else ""
        line = f"{COVERAGE_KEY}{source}: {count} of {total} parts included"
        return line, "incomplete" if count < total else ""

    @staticmethod
    def _coverage_notices(flags: set[str]) -> list[str]:
        """Pick the notices the raised flags call for.

        All notices belong with the coverage lines, ahead of the content: every reducer
        downstream cuts from the tail, so a trailing notice is the first thing lost.
        """
        notices = []
        if "incomplete" in flags:
            notices.append(INCOMPLETE_NOTICE)
        if "over_limit" in flags:
            notices.append(OVERLIMIT_NOTICE)
        if flags == {"unverified"}:
            # Unverified coverage is precisely where completeness cannot be vouched for, so
            # it must not be the one path that ships without the guardrail.
            notices.append(UNVERIFIED_NOTICE)
        return notices

    def process_llm_routing_index(self, query: str, kb_index):
        request_id = self.metadata.get(REQUEST_ID)
        llm = get_llm_by_credentials(llm_model=self.llm_model, request_id=request_id, streaming=False)
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

        docs_content = "\n".join(
            [
                f"\n{SOURCE_DOCUMENT_KEY}\n"
                f"{SOURCE_FIELD_KEY}{doc['title']}\n"
                f"{FILE_CONTENT_FIELD_KEY} \n{doc['content']}\n"
                for doc in documents
            ]
        )

        return str(selected_sections.sections) + "\n" + docs_content

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
