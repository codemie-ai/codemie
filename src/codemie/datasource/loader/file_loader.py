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

"""File with FileDatasourceLoader logic."""

import collections
import tempfile
import uuid
from typing import Any, Union, List, Optional

from codemie_tools.base.file_object import FileObject
from langchain_community.document_loaders import CSVLoader
from langchain_community.document_loaders import UnstructuredPowerPointLoader
from codemie.datasource.loader.pdf_plumber_loader import PDFPlumberLoader
from langchain_community.document_loaders.parsers import BaseImageBlobParser
from langchain_core.documents import Document
from langchain_markitdown import (
    DocxLoader,
    XlsxLoader,
    HtmlLoader,
    EpubLoader,
    IpynbLoader,
    OutlookMsgLoader,
    PlainTextLoader,
    AudioLoader,
    ImageLoader,
    ZipLoader,
)

from codemie.configs import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.rest_api.models.index import IndexKnowledgeBaseFileTypes

_LOADERS = {
    IndexKnowledgeBaseFileTypes.CSV.value: CSVLoader,
    IndexKnowledgeBaseFileTypes.PDF.value: PDFPlumberLoader,
    IndexKnowledgeBaseFileTypes.PPTX.value: UnstructuredPowerPointLoader,
    IndexKnowledgeBaseFileTypes.DOCX.value: DocxLoader,
    IndexKnowledgeBaseFileTypes.XLSX.value: XlsxLoader,
    IndexKnowledgeBaseFileTypes.HTML.value: HtmlLoader,
    IndexKnowledgeBaseFileTypes.EPUB.value: EpubLoader,
    IndexKnowledgeBaseFileTypes.IPYNB.value: IpynbLoader,
    IndexKnowledgeBaseFileTypes.MSG.value: OutlookMsgLoader,
    IndexKnowledgeBaseFileTypes.ZIP.value: ZipLoader,
    IndexKnowledgeBaseFileTypes.AUDIO.value: AudioLoader,
    IndexKnowledgeBaseFileTypes.IMAGE.value: ImageLoader,
}


class FilesDatasourceLoader(BaseDatasourceLoader):
    """
    FilesDatasourceLoader class to load data from supported file types.

    Supports various document formats including:
    - Text files (TXT, XML, YAML, YML, JSON) - handled directly
    - PDF files - using PDFPlumberLoader
    - Office documents (DOCX, PPTX, XLSX) - using specialized loaders
    - Web content (HTML)
    - E-books (EPUB)
    - Notebooks (IPYNB)
    - Email (MSG)
    - Archives (ZIP)
    - Media files (images, audio)

    Attributes:
        total_count_of_documents (int): Total count of documents to be loaded.
        file_repo (FileRepository): Repository for file operations.
        files_paths (list): List of file paths to be loaded.
        _loader_kwargs (dict): Additional arguments for specific file loaders.
        request_uuid (str): Request ID for tracking LLM usage.
    """

    def __init__(
        self,
        total_count_of_documents: int,
        files_paths: List[collections.namedtuple],
        csv_separator: str,
        request_uuid: Optional[str] = None,
    ):
        """
        Initialize the FilesDatasourceLoader.

        Args:
            total_count_of_documents (int): Total count of documents to be loaded.
            files_paths (list): List of file paths to be loaded.
            csv_separator (str): CSV delimiter.
            request_uuid (str, optional): Request UUID for tracking LLM usage.
        """
        self.total_count_of_documents = total_count_of_documents
        self.file_repo = FileRepositoryFactory.get_current_repository()
        self.files_paths = files_paths
        self.request_uuid = request_uuid
        self._loader_kwargs = {
            IndexKnowledgeBaseFileTypes.CSV.value: {"csv_args": {"delimiter": csv_separator}},
            IndexKnowledgeBaseFileTypes.PDF.value: {
                "mode": "page",
                "images_inner_format": "markdown-img",
                "extract_images": True,
                "extract_tables": "markdown",
            },
            IndexKnowledgeBaseFileTypes.XLSX.value: {"split_by_page": True},
        }

    def fetch_remote_stats(self) -> dict[str, Any]:
        """
        Fetch the remote statistics.

        Returns:
            dict: The remote statistics with the document count.
        """
        return {
            self.DOCUMENTS_COUNT_KEY: self.total_count_of_documents,
            self.TOTAL_DOCUMENTS_KEY: self.total_count_of_documents,
        }

    def lazy_load(self) -> Union[Document, List[Document]]:
        """
        This method loads the whole Document of a given file.
        Depending on the file type a different loader is used.

        Yields:
            Union[Document, List[Document]]: Yields documents loaded from the files.
        """
        for file_data in self.files_paths:
            file = self.file_repo.read_file(file_data.name, file_data.owner)
            file_ext = file.name.split('.')[-1].lower()
            yield self._lazy_load_documents(file, file_ext)

    def _lazy_load_documents(self, file: FileObject, file_ext: str) -> List[Document]:
        """
        Lazy load documents from the file based on the file extension.

        Args:
            file (FileObject): The file object.
            file_ext (str): The file extension.

        Returns:
            List[Document]: A list of documents loaded from the file.
        """
        documents = []
        loader_class = _LOADERS.get(file_ext)
        if not loader_class:
            loader_class = PlainTextLoader
        with tempfile.NamedTemporaryFile(suffix=f".{file_ext}") as temp_file:
            temp_file.write(file.bytes_content())
            temp_file.flush()
            loader_kwargs = self._loader_kwargs.get(file_ext, {})

            # Configure image processing for PDF files if multimodal LLM is available
            if file_ext == IndexKnowledgeBaseFileTypes.PDF.value:
                from codemie.service.llm_service.llm_service import llm_service

                multimodal_llms = llm_service.get_multimodal_llms()
                images_parser: BaseImageBlobParser
                if multimodal_llms:
                    # Use the first available multimodal LLM for image processing
                    # Pass request_uuid to track token usage from document processing
                    llm = get_llm_by_credentials(
                        llm_model=multimodal_llms[0],
                        streaming=False,
                        request_id=self.request_uuid if self.request_uuid else str(uuid.uuid4()),
                    )
                    from langchain_community.document_loaders.parsers import LLMImageBlobParser

                    images_parser = LLMImageBlobParser(model=llm)
                else:
                    from langchain_community.document_loaders.parsers import TesseractBlobParser

                    images_parser = TesseractBlobParser()

                loader_kwargs["images_parser"] = images_parser

            loader = loader_class(temp_file.name, **loader_kwargs)
            try:
                for document in loader.lazy_load():
                    document.metadata["source"] = file.name
                    # Remove file_path if it contains temp path, or set it to original name
                    if "file_path" in document.metadata:
                        document.metadata["file_path"] = file.name
                    documents.append(document)
            except UnicodeDecodeError as e:
                logger.warning(
                    f"Failed to load file due to encoding error: {file.name}. "
                    f"File cannot be decoded with default encoding: {e}",
                    exc_info=True,
                )
            except ValueError:
                logger.warning(f"Unsupported file type: {file_ext} for file {file.name}", exc_info=True)

            return documents
