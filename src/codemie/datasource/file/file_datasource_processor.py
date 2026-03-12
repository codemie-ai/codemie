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

"""File with FileDatasourceProcessor logic."""

import json
from collections import defaultdict
from collections import namedtuple
from typing import Optional, Tuple, List, Union
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_text_splitters import RecursiveJsonSplitter
from codemie.configs import logger
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.models.index import IndexKnowledgeBaseFileTypes
from codemie.core.models import KnowledgeBase
from codemie.datasource.base_datasource_processor import (
    BaseDatasourceProcessor,
    DatasourceProcessorCallback,
)
from codemie.datasource.datasources_config import JSON_CONFIG
from codemie.datasource.datasources_config import FILE_CONFIG
from codemie.rest_api.security.user import User
from codemie.datasource.loader.file_loader import FilesDatasourceLoader
from langchain_core.documents import Document
from codemie.rest_api.models.index import IndexInfo

FILE_PATH_DATA_NT = namedtuple("FILE_PATH_NT", ["name", "owner"])


class FileDatasourceProcessor(BaseDatasourceProcessor):
    INDEX_TYPE = "knowledge_base_file"

    STARTED_MESSAGE_TEMPLATE = "Indexing of {} has started in the background"

    def __init__(
        self,
        datasource_name: str,
        user: User,
        files_paths: List[FILE_PATH_DATA_NT],
        project_name: str,
        description: str = "",
        project_space_visible: bool = False,
        index: IndexInfo = None,
        callbacks: Optional[list[DatasourceProcessorCallback]] = None,
        request_uuid: Optional[str] = None,
        csv_separator: str = ',',
        csv_start_row: int = 1,
        csv_rows_per_document: int = 1,
        embedding_model: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
    ):
        """
        Initialize FileDatasourceProcessor with the provided parameters.

        Args:
            datasource_name (str): Name of the data source.
            user (User): The user initiating the processing.
            files_paths (List[FILE_PATH_DATA_NT]): List of file paths to be processed.
            project_name (str): Name of the project.
            description (str): Description of the project.
            project_space_visible (bool): Visibility of the project space.
            csv_separator (str): Separator used in CSV files.
            csv_start_row (int): Starting row for processing CSV files.
            csv_rows_per_document (int): Number of rows per document for CSV files.
        """
        self.project_name = project_name
        self.description = description
        self.project_space_visible = project_space_visible
        self.files_paths = files_paths
        self.index = index
        self.embedding_model = embedding_model

        # Required for CSV processing
        self.csv_separator = csv_separator
        self.csv_start_row = csv_start_row
        self.csv_rows_per_document = csv_rows_per_document
        super().__init__(
            datasource_name=datasource_name,
            user=user,
            index=index,
            callbacks=callbacks,
            request_uuid=request_uuid,
            guardrail_assignments=guardrail_assignments,
        )

    @property
    def started_message(self) -> str:
        """Message to be displayed when indexing starts"""
        return self.STARTED_MESSAGE_TEMPLATE.format(self.datasource_name)

    @property
    def _index_name(self) -> str:
        """
        Construct the index name based on the data source name.

        Returns:
            str: The constructed index name.
        """
        return KnowledgeBase(name=f"{self.project_name}-{self.datasource_name}", type=self.INDEX_TYPE).get_identifier()

    def _init_index(self) -> None:
        """
        Initialize the index for the knowledge base.

        This method creates the index from the FileDatasourceProcessor instance and the user.
        """
        if not self.index:
            self.index = IndexInfo.create_from_file_processor(self, self.user)

        self._assign_and_sync_guardrails()

    def _init_loader(self) -> BaseDatasourceLoader:
        """
        Initialize the file loader for processing files.

        This method sets up the FilesDatasourceLoader with the total document count, file paths, and CSV separator.
        """
        return FilesDatasourceLoader(
            total_count_of_documents=len(self.files_paths),
            files_paths=self.files_paths,
            csv_separator=self.csv_separator,
            request_uuid=self.request_uuid,
        )

    def _pre_process_csv(self, documents: List[Document]) -> List[Document]:
        """
        Preprocesses a list of CSV documents by adding metadata to each document.

        This method processes each document in the provided list, adding a 'row' metadata
        field to each document to indicate its row number in the CSV file. This is useful
        for tracking the origin of each document in the context of the source CSV file.
        It also adds the rows per Document as requested by user via UI.

        Args:
            documents (List[Document]): A list of documents to preprocess. Each document
                                        should represent a row of the CSV file.

        Returns:
            List[Document]: The list of preprocessed documents with added 'row' metadata.
        """
        start = 0
        pre_processed_documents = []
        end = self.csv_rows_per_document
        counter = self.csv_start_row
        source = documents[0].metadata.get("source")
        while documents[start:end]:
            doc_window = documents[start:end]
            updated_document = Document(
                page_content="\n\n".join([doc.page_content for doc in doc_window]),
                metadata={"source": source, "row": f"row {counter}"},
            )
            if updated_document.page_content:
                pre_processed_documents.append(updated_document)
            start += self.csv_rows_per_document
            end += self.csv_rows_per_document
            counter += 1
        return pre_processed_documents

    def _process_chunks(self, documents: List[Document]) -> None:
        counter = 1
        for document in documents:
            document.metadata["chunk_num"] = counter
            counter += 1

    @classmethod
    def _get_splitter(cls, document: Document = None) -> Union[RecursiveCharacterTextSplitter, RecursiveJsonSplitter]:
        config = FILE_CONFIG
        if document:
            file_type = document.metadata.get("file_path", document.metadata.get("source")).split(".")[-1]
            if file_type == IndexKnowledgeBaseFileTypes.JSON.value:
                config = JSON_CONFIG

        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="o200k_base",
            chunk_size=config.chunk_size,
            disallowed_special={},
            chunk_overlap=config.chunk_overlap,
        )

    def _process_single_document(
        self, document: Document, single_docs: List[Document], json_docs: List[Document]
    ) -> None:
        """
        Process a single document and add it to appropriate list.

        Args:
            document: Document to process
            single_docs: List to append non-JSON documents
            json_docs: List to append JSON documents
        """
        source = document.metadata.get(self.SOURCE)
        if not source:
            logger.warning(
                "Skipping document with missing source metadata",
                extra={"datasource_name": self.datasource_name},
            )
            return

        file_ext = source.split(".")[-1]
        if file_ext == IndexKnowledgeBaseFileTypes.JSON.value:
            json_docs.append(document)
        else:
            single_docs.append(document)

    def _process_document_list(self, documents: List[Document], list_of_docs: List[Document]) -> None:
        """
        Process a list of documents and add to list_of_docs.

        Args:
            documents: List of documents to process
            list_of_docs: List to append processed documents
        """
        if not documents:
            logger.warning(
                "Skipping empty document list in batch processing",
                extra={"datasource_name": self.datasource_name},
            )
            return

        source = documents[0].metadata.get(self.SOURCE)
        if not source:
            logger.warning(
                "Skipping document list with missing source metadata",
                extra={"datasource_name": self.datasource_name},
            )
            return

        file_ext = source.split(".")[-1]
        if file_ext == IndexKnowledgeBaseFileTypes.CSV.value:
            pre_processed = self._pre_process_csv(documents)
            list_of_docs.extend(pre_processed)
        else:
            list_of_docs.extend(documents)

    def _segregate_documents_input(
        self, documents: Union[List[List[Document]], List[Document]]
    ) -> Tuple[List[Document], List[Document], List[Document]]:
        """
        Segregates the input documents into two lists: one containing individual documents and the other
        containing lists of documents.

        This method processes the provided `documents` input and separates it into two distinct lists:
        - `single_docs`: A list containing individual `Document` instances.
        - `list_of_docs`: A list containing the documents from lists of `Document` instances, with special
            handling for CSV files.
        - `json_docs`: A list containing individual `Document` instances, with json data.

        Args:
            documents (Union[List[List[Document]], List[Document]]): A collection of documents, which can be either a
                list of individual `Document` instances or a list of lists of `Document` instances.

        Returns:
            Tuple[List[Document], List[Document]]: A tuple containing two lists:
                - list_of_docs: A list containing the documents from lists of `Document` instances,
                    with CSV files pre-processed.
                - single_docs: A list containing individual `Document` instances.
                - json_docs: A list containing 'Document's holding json data.
        """
        list_of_docs = []
        single_docs = []
        json_docs = []

        for d in documents:
            if isinstance(d, Document):
                self._process_single_document(d, single_docs, json_docs)
            else:
                self._process_document_list(d, list_of_docs)

        return list_of_docs, single_docs, json_docs

    def _split_json_documents(self, docs: List[Document]) -> dict[str, List[Document]]:
        """
        This method processes each JSON document in the provided list, splits the documents into
        smaller chunks using a text splitter, and stores the resulting chunks in a dictionary.
        Each key in the dictionary corresponds to the original document's file path or source,
        and the value is a list of chunked documents derived from that source.

        Args:
            docs (List[Document]): A list of JSON documents to be split into smaller chunks.
             Each document contains JSON content that will be split.

        Returns:
            Dict[str, List[Document]]: A dictionary where each key is the file path or source of the original
            document, and the value is a list of chunked documents.
        """
        json_documents_dict = defaultdict(list)
        for document in docs:
            splitter = self._get_splitter(document)
            raw_data = json.loads(document.page_content)
            raw_docs = [Document(page_content=item["content"], metadata=item["metadata"]) for item in raw_data]
            for doc in raw_docs:
                split_docs = []
                split_text = splitter.split_text(doc.page_content)
                for chunk in split_text:
                    chunk_doc = Document(page_content=chunk, metadata=doc.metadata)
                    if "source" not in chunk_doc.metadata:
                        chunk_doc.metadata["source"] = document.metadata.get(self.SOURCE)
                    split_docs.append(chunk_doc)
                document_key = document.metadata.get("file_path", document.metadata.get(self.SOURCE))
                json_documents_dict[document_key].extend(split_docs)
        for callback in self.callbacks:
            callback.on_split_documents(docs)
        # Add chunk number.
        for docs in json_documents_dict.values():
            if len(docs) > 1:
                self._process_chunks(docs)
        return json_documents_dict

    def _split_list_with_documents(self, docs: List[Document]) -> dict[str, List[Document]]:
        """
        Splits a list of documents into smaller chunks and organizes them into a dictionary.

        This method processes each document in the provided list, splits the documents into
        smaller chunks using a text splitter, and stores the resulting chunks in a dictionary.
        Each key in the dictionary corresponds to the original document's file path or source,
        and the value is a list of chunked documents derived from that source.

        Args:
            docs (List[Document]): A list of documents to be split into smaller chunks.
            Each document contains text content that will be split.

        Returns:
            Dict[str, List[Document]]: A dictionary where each key is the file path or source of the original
            document, and the value is a list of chunked documents.
        """
        list_documents_dict = defaultdict(list)
        for document in docs:
            split_chunks = self._get_splitter(document).split_text(document.page_content)
            chunk_list = []
            for _, chunk in enumerate(split_chunks):
                chunk_metadata = document.metadata.copy()
                chunk_list.append(Document(page_content=chunk, metadata=chunk_metadata))
            document_key = document.metadata.get("file_path", document.metadata.get(self.SOURCE))
            list_documents_dict[document_key].extend(chunk_list)
        for callback in self.callbacks:
            callback.on_split_documents(docs)
        # Add chunk number.
        for docs in list_documents_dict.values():
            if len(docs) > 1:
                self._process_chunks(docs)
        return list_documents_dict

    def _split_documents(self, docs: list[Document]) -> dict[str, list[Document]]:
        """
        Splits documents into smaller chunks based on the specified chunk size and overlap.

        This method takes a list of documents, splits each document into smaller chunks using
        a text splitter, and organizes the resulting chunks into a dictionary. Each key in the
        dictionary corresponds to the original document's source, and the value is a list of
        chunked documents derived from that source.

        Args:
            docs (List[Document]): A list of documents to be split into smaller chunks.
            Each document contains text content that will be split.

        Returns:
            Dict[str, List[Document]]: A dictionary where each key is the source of the original
            document, and the value is a list of chunked documents.
        """
        list_of_docs, single_docs, json_docs = self._segregate_documents_input(docs)

        # Process list of Document.
        list_documents_dict = self._split_list_with_documents(list_of_docs)
        # Process json data.
        json_documents_dict = self._split_json_documents(json_docs)
        # Process single Document instances.
        single_document_dict = super()._split_documents(single_docs)

        return list_documents_dict | json_documents_dict | single_document_dict
