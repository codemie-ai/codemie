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

"""Shared file extraction utility used by FilesDatasourceLoader and SharePointLoader."""

from __future__ import annotations

import os
import tempfile
import uuid

from langchain_community.document_loaders import CSVLoader, UnstructuredPowerPointLoader
from langchain_community.document_loaders.parsers import BaseImageBlobParser
from langchain_core.documents import Document
from langchain_markitdown import (
    AudioLoader,
    DocxLoader,
    EpubLoader,
    HtmlLoader,
    ImageLoader,
    IpynbLoader,
    OutlookMsgLoader,
    PlainTextLoader,
    XlsxLoader,
    ZipLoader,
)

from codemie.configs import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.datasource.loader.pdf_plumber_loader import PDFPlumberLoader
from codemie.rest_api.models.index import IndexKnowledgeBaseFileTypes

LOADERS: dict[str, type] = {
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
    IndexKnowledgeBaseFileTypes.JPEG.value: ImageLoader,
    IndexKnowledgeBaseFileTypes.PNG.value: ImageLoader,
}

DEFAULT_LOADER_KWARGS: dict[str, dict] = {
    IndexKnowledgeBaseFileTypes.PDF.value: {
        "mode": "page",
        "images_inner_format": "markdown-img",
        "extract_images": True,
        "extract_tables": "markdown",
    },
    IndexKnowledgeBaseFileTypes.XLSX.value: {"split_by_page": True},
}


def _build_pdf_images_parser(request_uuid: str | None) -> BaseImageBlobParser:
    from codemie.service.llm_service.llm_service import llm_service

    multimodal_llms = llm_service.get_multimodal_llms()
    if multimodal_llms:
        from langchain_community.document_loaders.parsers import LLMImageBlobParser

        llm = get_llm_by_credentials(
            llm_model=multimodal_llms[0],
            streaming=False,
            request_id=request_uuid or str(uuid.uuid4()),
        )
        return LLMImageBlobParser(model=llm)

    from langchain_community.document_loaders.parsers import TesseractBlobParser

    return TesseractBlobParser()


def is_binary_extractable(file_path: str) -> bool:
    """Return True if the file is handled by extract_documents_from_bytes.

    Accepts a file name or path (e.g. 'report.pdf', '/tmp/image.PNG').
    """
    ext = os.path.splitext(file_path)[1].lower().lstrip('.')
    return ext in LOADERS


def extract_documents_from_bytes(
    file_bytes: bytes,
    file_name: str,
    request_uuid: str | None = None,
    csv_separator: str = ",",
) -> list[Document]:
    """
    Extract LangChain Documents from raw bytes using the appropriate loader.

    Args:
        file_bytes: Raw file content.
        file_name: Original file name used to determine loader and rewrite metadata.
        request_uuid: Optional request ID for LLM token-usage tracking.
        csv_separator: CSV column delimiter (default ",").

    Returns:
        List of LangChain Document objects.
    """
    file_ext = os.path.splitext(file_name)[1].lower().lstrip('.')
    documents: list[Document] = []
    loader_class = LOADERS.get(file_ext, PlainTextLoader)
    loader_kwargs: dict = dict(DEFAULT_LOADER_KWARGS.get(file_ext, {}))

    if file_ext == IndexKnowledgeBaseFileTypes.CSV.value:
        loader_kwargs["csv_args"] = {"delimiter": csv_separator}

    if file_ext == IndexKnowledgeBaseFileTypes.PDF.value:
        loader_kwargs["images_parser"] = _build_pdf_images_parser(request_uuid)

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{file_ext}", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            temp_path = tmp.name
        loader = loader_class(temp_path, **loader_kwargs)
        try:
            for document in loader.lazy_load():
                document.metadata["source"] = file_name
                if "file_path" in document.metadata:
                    document.metadata["file_path"] = file_name
                documents.append(document)
        except UnicodeDecodeError as e:
            logger.warning(
                f"Failed to load file due to encoding error: {file_name}. "
                f"File cannot be decoded with default encoding: {e}",
                exc_info=True,
            )
        except ValueError:
            logger.warning(f"Unsupported file type: {file_ext} for file {file_name}", exc_info=True)
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file {temp_path}: {e}")

    return documents
