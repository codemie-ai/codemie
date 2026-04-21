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

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.documents import Document

from codemie.datasource.loader.azure_devops_work_item_loader import (
    MAX_ATTACHMENT_BYTES,
    AzureDevOpsWorkItemLoader,
    _strip_html,
)
from codemie.datasource.exceptions import (
    InvalidQueryException,
    MissingIntegrationException,
    UnauthorizedException,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_work_item_client():
    """Mock WorkItemTrackingClient for testing."""
    return MagicMock()


@pytest.fixture
def mock_connection(mock_work_item_client):
    """Mock Azure DevOps Connection."""
    mock_conn = MagicMock()
    mock_conn.clients_v7_1.get_work_item_tracking_client.return_value = mock_work_item_client
    return mock_conn


@pytest.fixture
def loader():
    """Create a basic loader instance."""
    return AzureDevOpsWorkItemLoader(
        base_url="https://dev.azure.com",
        wiql_query="SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project",
        access_token="fake-token",
        organization="test-org",
        project="test-project",
        batch_size=50,
    )


@pytest.fixture
def loader_with_session(loader):
    """Loader with a mocked HTTP session."""
    loader._session = MagicMock()
    return loader


@pytest.fixture
def sample_work_item():
    """A sample work item dict as returned by _fetch_work_item."""
    return {
        "id": 42,
        "url": "https://dev.azure.com/test-org/test-project/_apis/wit/workitems/42",
        "fields": {
            "System.Id": 42,
            "System.Title": "Fix login bug",
            "System.Description": "<p>Users can't login</p>",
            "System.WorkItemType": "Bug",
            "System.State": "Active",
            "System.AssignedTo": {"displayName": "Jane Doe"},
            "System.AreaPath": "MyProject\\Backend",
            "System.IterationPath": "MyProject\\Sprint 1",
            "System.Tags": "auth; critical",
            "System.CreatedDate": "2024-01-01T00:00:00Z",
            "System.ChangedDate": "2024-01-05T00:00:00Z",
            "Microsoft.VSTS.Common.Priority": 1,
        },
    }


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestLoaderInitialization:
    def test_basic_initialization(self, loader):
        assert loader.base_url == "https://dev.azure.com/test-org"
        assert loader.access_token == "fake-token"
        assert loader.organization == "test-org"
        assert loader.project == "test-project"
        assert loader.batch_size == 50
        assert loader._chat_model is None

    def test_organization_not_duplicated_in_url(self):
        ldr = AzureDevOpsWorkItemLoader(
            base_url="https://dev.azure.com/test-org",
            wiql_query="",
            access_token="t",
            organization="test-org",
            project="p",
        )
        assert ldr.base_url == "https://dev.azure.com/test-org"

    def test_trailing_slash_removed(self):
        ldr = AzureDevOpsWorkItemLoader(
            base_url="https://dev.azure.com/",
            wiql_query="",
            access_token="t",
            organization="test-org",
            project="p",
        )
        assert ldr.base_url == "https://dev.azure.com/test-org"
        assert not ldr.base_url.endswith("/")

    def test_default_wiql_query_when_empty(self):
        ldr = AzureDevOpsWorkItemLoader(
            base_url="https://dev.azure.com",
            wiql_query="",
            access_token="t",
            organization="org",
            project="p",
        )
        assert "SELECT" in ldr.wiql_query

    def test_chat_model_stored(self):
        mock_model = MagicMock()
        ldr = AzureDevOpsWorkItemLoader(
            base_url="https://dev.azure.com",
            wiql_query="",
            access_token="t",
            organization="org",
            project="p",
            chat_model=mock_model,
        )
        assert ldr._chat_model is mock_model

    def test_default_index_flags(self, loader):
        assert loader.index_comments is True
        assert loader.index_attachments is True

    def test_index_flags_can_be_disabled(self):
        ldr = AzureDevOpsWorkItemLoader(
            base_url="https://dev.azure.com",
            wiql_query="",
            access_token="t",
            organization="org",
            project="p",
            index_comments=False,
            index_attachments=False,
        )
        assert ldr.index_comments is False
        assert ldr.index_attachments is False


# ---------------------------------------------------------------------------
# Client & session
# ---------------------------------------------------------------------------


class TestClientAndSession:
    @patch("codemie.datasource.loader.azure_devops_work_item_loader.Connection")
    def test_init_client(self, mock_connection_class, loader):
        mock_conn = MagicMock()
        mock_connection_class.return_value = mock_conn

        loader._init_client()

        assert loader._connection is not None
        assert loader._work_item_client is not None
        assert loader._session is not None
        mock_connection_class.assert_called_once()
        mock_conn.clients_v7_1.get_work_item_tracking_client.assert_called_once()

    def test_init_session(self, loader):
        loader._init_session()
        assert loader._session is not None

    def test_get_rest_api_base_url(self, loader):
        assert loader._get_rest_api_base_url() == "https://dev.azure.com/test-org/test-project/_apis"

    def test_create_auth_header(self, loader):
        import base64

        header = loader._create_auth_header()
        assert "Authorization" in header
        assert header["Authorization"].startswith("Basic ")
        expected_token = base64.b64encode(b":fake-token").decode()
        assert header["Authorization"] == f"Basic {expected_token}"
        assert header["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# Credential validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_token_raises(self):
        ldr = AzureDevOpsWorkItemLoader(
            base_url="https://dev.azure.com",
            wiql_query="",
            access_token="",
            organization="org",
            project="p",
        )
        with pytest.raises(MissingIntegrationException):
            ldr._validate_creds()

    @patch("codemie.datasource.loader.azure_devops_work_item_loader.Connection")
    def test_invalid_credentials_raises(self, mock_connection_class, loader):
        mock_conn = MagicMock()
        mock_client = MagicMock()
        mock_client.query_by_wiql.side_effect = Exception("Authentication failed")
        mock_conn.clients_v7_1.get_work_item_tracking_client.return_value = mock_client
        mock_connection_class.return_value = mock_conn

        loader._init_client()

        with pytest.raises(UnauthorizedException, match="AzureDevOps Work Items"):
            loader._validate_creds()


# ---------------------------------------------------------------------------
# WIQL
# ---------------------------------------------------------------------------


class TestWiql:
    def test_invalid_query_raises(self, loader, mock_work_item_client):
        loader._work_item_client = mock_work_item_client
        mock_work_item_client.query_by_wiql.side_effect = Exception("TF51005: invalid syntax")

        with pytest.raises(InvalidQueryException):
            loader._run_wiql("bad query")


# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_basic_html(self):
        assert _strip_html("<p>Hello <b>World</b></p>") == "Hello World"

    def test_empty_string(self):
        assert _strip_html("") == ""

    def test_no_tags(self):
        assert _strip_html("plain text") == "plain text"


# ---------------------------------------------------------------------------
# _transform_to_doc
# ---------------------------------------------------------------------------


class TestTransformToDoc:
    def test_basic_transform(self, loader, sample_work_item):
        doc = loader._transform_to_doc(sample_work_item)

        assert isinstance(doc, Document)
        assert "Fix login bug" in doc.page_content
        assert "Users can't login" in doc.page_content
        assert doc.metadata["work_item_id"] == 42
        assert doc.metadata["work_item_type"] == "Bug"
        assert doc.metadata["state"] == "Active"
        assert doc.metadata["title"] == "Fix login bug"
        assert doc.metadata["source"] == "https://dev.azure.com/test-org/test-project/_workitems/edit/42"

    def test_assigned_to_string(self, loader):
        item = {
            "id": 1,
            "url": "",
            "fields": {
                "System.Title": "Title",
                "System.AssignedTo": "Alice",
                "System.WorkItemType": "Task",
                "System.State": "New",
            },
        }
        doc = loader._transform_to_doc(item)
        assert "Assigned To: Alice" in doc.page_content


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class TestComments:
    def test_get_comments_success(self, loader, mock_work_item_client):
        loader._work_item_client = mock_work_item_client

        mock_comment = MagicMock()
        mock_comment.as_dict.return_value = {
            "id": 1,
            "text": "<p>Looks good</p>",
            "created_by": {"display_name": "Alice"},
            "created_date": "2024-01-01T00:00:00Z",
            "modified_date": "2024-01-01T00:00:00Z",
        }
        mock_result = MagicMock()
        mock_result.comments = [mock_comment]
        mock_work_item_client.get_comments.return_value = mock_result

        comments = loader._get_work_item_comments(42)

        assert len(comments) == 1
        assert comments[0]["comment_id"] == 1
        assert comments[0]["content"] == "Looks good"
        assert comments[0]["author"] == "Alice"

    def test_get_comments_error_returns_empty(self, loader, mock_work_item_client):
        loader._work_item_client = mock_work_item_client
        mock_work_item_client.get_comments.side_effect = Exception("API error")

        comments = loader._get_work_item_comments(42)

        assert comments == []

    def test_get_comments_empty_result(self, loader, mock_work_item_client):
        loader._work_item_client = mock_work_item_client
        mock_result = MagicMock()
        mock_result.comments = []
        mock_work_item_client.get_comments.return_value = mock_result

        comments = loader._get_work_item_comments(42)

        assert comments == []

    def test_get_comments_none_comments(self, loader, mock_work_item_client):
        loader._work_item_client = mock_work_item_client
        mock_result = MagicMock()
        mock_result.comments = None
        mock_work_item_client.get_comments.return_value = mock_result

        comments = loader._get_work_item_comments(42)

        assert comments == []

    def test_build_comments_doc_empty_list(self, loader, sample_work_item):
        result = loader._build_comments_doc(sample_work_item, [])
        assert result is None

    def test_build_comments_doc_all_blank_content(self, loader, sample_work_item):
        comments = [{"author": "Alice", "created_date": "2024-01-01", "content": "   "}]
        result = loader._build_comments_doc(sample_work_item, comments)
        assert result is None

    def test_build_comments_doc_with_comments(self, loader, sample_work_item):
        comments = [
            {"author": "Alice", "created_date": "2024-01-01", "content": "Looks good!"},
            {"author": "Bob", "created_date": "2024-01-02", "content": "Thanks!"},
        ]
        result = loader._build_comments_doc(sample_work_item, comments)

        assert result is not None
        assert isinstance(result, Document)
        assert "Looks good!" in result.page_content
        assert "Thanks!" in result.page_content
        assert "Alice" in result.page_content
        assert result.metadata["content_type"] == "comments"
        assert result.metadata["work_item_id"] == 42
        assert result.metadata["work_item_type"] == "Bug"
        assert result.metadata["state"] == "Active"
        assert result.metadata["title"] == "Fix login bug"
        assert result.metadata["source"].endswith("#comments")
        assert "summary" in result.metadata
        assert "Comments" in result.metadata["summary"]

    def test_build_comments_doc_summary_with_many_authors(self, loader, sample_work_item):
        comments = [{"author": f"Author{i}", "created_date": "2024-01-01", "content": f"Comment {i}"} for i in range(5)]
        result = loader._build_comments_doc(sample_work_item, comments)

        assert result is not None
        assert "others" in result.metadata["summary"]


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


class TestAttachments:
    def test_get_attachments_success(self, loader, mock_work_item_client):
        loader._work_item_client = mock_work_item_client

        mock_rel = MagicMock()
        mock_rel.as_dict.return_value = {
            "rel": "AttachedFile",
            "url": "https://dev.azure.com/_apis/wit/attachments/abc123",
            "attributes": {"name": "spec.pdf", "resourceSize": 2048, "comment": "Spec document"},
        }
        mock_item = MagicMock()
        mock_item.relations = [mock_rel]
        mock_work_item_client.get_work_item.return_value = mock_item

        attachments = loader._get_work_item_attachments(42)

        assert len(attachments) == 1
        assert attachments[0]["name"] == "spec.pdf"
        assert attachments[0]["url"] == "https://dev.azure.com/_apis/wit/attachments/abc123"
        assert attachments[0]["content_type"] == "application/pdf"

    def test_get_attachments_skips_non_attached_file_relations(self, loader, mock_work_item_client):
        loader._work_item_client = mock_work_item_client

        mock_rel_parent = MagicMock()
        mock_rel_parent.as_dict.return_value = {"rel": "System.LinkTypes.Hierarchy-Reverse", "url": "..."}
        mock_rel_file = MagicMock()
        mock_rel_file.as_dict.return_value = {
            "rel": "AttachedFile",
            "url": "https://att-url",
            "attributes": {"name": "file.txt"},
        }
        mock_item = MagicMock()
        mock_item.relations = [mock_rel_parent, mock_rel_file]
        mock_work_item_client.get_work_item.return_value = mock_item

        attachments = loader._get_work_item_attachments(42)

        assert len(attachments) == 1
        assert attachments[0]["name"] == "file.txt"

    def test_get_attachments_no_relations(self, loader, mock_work_item_client):
        loader._work_item_client = mock_work_item_client

        mock_item = MagicMock()
        mock_item.relations = None
        mock_work_item_client.get_work_item.return_value = mock_item

        attachments = loader._get_work_item_attachments(42)

        assert attachments == []

    def test_get_attachments_error_returns_empty(self, loader, mock_work_item_client):
        loader._work_item_client = mock_work_item_client
        mock_work_item_client.get_work_item.side_effect = Exception("API error")

        attachments = loader._get_work_item_attachments(42)

        assert attachments == []

    def test_download_attachment_no_url(self, loader):
        assert loader._download_attachment("", "file.pdf") is None

    def test_download_attachment_no_session(self, loader):
        loader._session = None
        assert loader._download_attachment("https://example.com/att", "file.pdf") is None

    def test_download_attachment_success(self, loader_with_session):
        mock_response = MagicMock()
        mock_response.content = b"binary data"
        loader_with_session._session.get.return_value = mock_response

        result = loader_with_session._download_attachment("https://att-url", "file.pdf")

        assert result == b"binary data"

    def test_download_attachment_empty_content(self, loader_with_session):
        mock_response = MagicMock()
        mock_response.content = b""
        loader_with_session._session.get.return_value = mock_response

        result = loader_with_session._download_attachment("https://att-url", "file.pdf")

        assert result is None

    def test_download_attachment_http_error(self, loader_with_session):
        loader_with_session._session.get.side_effect = Exception("Timeout")

        result = loader_with_session._download_attachment("https://att-url", "file.pdf")

        assert result is None

    def test_download_attachment_passes_timeout(self, loader_with_session):
        mock_response = MagicMock()
        mock_response.content = b"data"
        loader_with_session._session.get.return_value = mock_response

        loader_with_session._download_attachment("https://att-url", "file.pdf")

        loader_with_session._session.get.assert_called_once_with("https://att-url", timeout=60)


# ---------------------------------------------------------------------------
# _build_attachment_doc
# ---------------------------------------------------------------------------


class TestBuildAttachmentDoc:
    def test_no_url_returns_none(self, loader, sample_work_item):
        attachment = {"name": "f.pdf", "url": "", "content_type": "application/pdf"}
        assert loader._build_attachment_doc(sample_work_item, attachment) is None

    def test_download_fails_returns_none(self, loader, sample_work_item):
        attachment = {"name": "f.pdf", "url": "https://att-url", "content_type": "application/pdf"}
        loader._download_attachment = MagicMock(return_value=None)

        assert loader._build_attachment_doc(sample_work_item, attachment) is None

    def test_no_text_extracted_returns_none(self, loader, sample_work_item):
        attachment = {"name": "f.pdf", "url": "https://att-url", "content_type": "application/pdf"}
        loader._download_attachment = MagicMock(return_value=b"content")
        loader._extract_attachment_text = MagicMock(return_value="   ")

        assert loader._build_attachment_doc(sample_work_item, attachment) is None

    def test_success(self, loader, sample_work_item):
        attachment = {"name": "spec.pdf", "url": "https://att-url", "content_type": "application/pdf"}
        loader._download_attachment = MagicMock(return_value=b"pdf bytes")
        loader._extract_attachment_text = MagicMock(return_value="Extracted PDF text")

        result = loader._build_attachment_doc(sample_work_item, attachment)

        assert result is not None
        assert isinstance(result, Document)
        assert result.page_content == "Extracted PDF text"
        assert result.metadata["content_type"] == "attachment"
        assert result.metadata["attachment_name"] == "spec.pdf"
        assert result.metadata["attachment_mime_type"] == "application/pdf"
        assert result.metadata["work_item_id"] == 42
        assert result.metadata["work_item_type"] == "Bug"
        assert result.metadata["state"] == "Active"
        assert result.metadata["title"] == "Fix login bug"
        assert "#attachment-spec.pdf" in result.metadata["source"]
        assert "attachment_summary" in result.metadata
        assert "summary" in result.metadata
        assert "attachment" in result.metadata["summary"].lower()

    def test_summary_truncated_for_long_text(self, loader, sample_work_item):
        attachment = {"name": "big.pdf", "url": "https://att-url", "content_type": "application/pdf"}
        long_text = "A" * 400
        loader._download_attachment = MagicMock(return_value=b"bytes")
        loader._extract_attachment_text = MagicMock(return_value=long_text)

        result = loader._build_attachment_doc(sample_work_item, attachment)

        assert result.metadata["attachment_summary"].endswith("...")
        assert len(result.metadata["attachment_summary"]) == 303  # 300 + "..."

    def test_oversized_attachment_returns_none(self, loader, sample_work_item):
        attachment = {
            "name": "video.mp4",
            "url": "https://att-url",
            "content_type": "video/mp4",
            "size": MAX_ATTACHMENT_BYTES + 1,
        }
        loader._download_attachment = MagicMock()

        result = loader._build_attachment_doc(sample_work_item, attachment)

        assert result is None
        loader._download_attachment.assert_not_called()

    def test_attachment_at_exact_size_limit_proceeds(self, loader, sample_work_item):
        attachment = {
            "name": "doc.pdf",
            "url": "https://att-url",
            "content_type": "application/pdf",
            "size": MAX_ATTACHMENT_BYTES,
        }
        loader._download_attachment = MagicMock(return_value=b"bytes")
        loader._extract_attachment_text = MagicMock(return_value="text")

        result = loader._build_attachment_doc(sample_work_item, attachment)

        loader._download_attachment.assert_called_once()
        assert result is not None


# ---------------------------------------------------------------------------
# Text extraction dispatch
# ---------------------------------------------------------------------------


class TestExtractAttachmentText:
    def test_dispatches_to_image_by_mime(self, loader):
        loader._extract_image_text = MagicMock(return_value="OCR result")
        result = loader._extract_attachment_text(b"bytes", "image/png", "pic.png")
        loader._extract_image_text.assert_called_once_with(b"bytes")
        assert result == "OCR result"

    def test_dispatches_to_image_by_extension(self, loader):
        loader._extract_image_text = MagicMock(return_value="OCR")
        loader._extract_attachment_text(b"bytes", "application/octet-stream", "photo.jpg")
        loader._extract_image_text.assert_called_once()

    def test_dispatches_to_pdf(self, loader):
        loader._extract_pdf_text = MagicMock(return_value="PDF text")
        result = loader._extract_attachment_text(b"bytes", "application/pdf", "doc.pdf")
        loader._extract_pdf_text.assert_called_once_with(b"bytes")
        assert result == "PDF text"

    def test_dispatches_to_docx(self, loader):
        loader._extract_docx_text = MagicMock(return_value="Word text")
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        result = loader._extract_attachment_text(b"bytes", mime, "file.docx")
        loader._extract_docx_text.assert_called_once_with(b"bytes", "file.docx")
        assert result == "Word text"

    def test_dispatches_to_xlsx(self, loader):
        loader._extract_xlsx_text = MagicMock(return_value="Table text")
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        result = loader._extract_attachment_text(b"bytes", mime, "data.xlsx")
        loader._extract_xlsx_text.assert_called_once_with(b"bytes")
        assert result == "Table text"

    def test_dispatches_to_pptx(self, loader):
        loader._extract_pptx_text = MagicMock(return_value="Slide text")
        mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        result = loader._extract_attachment_text(b"bytes", mime, "slides.pptx")
        loader._extract_pptx_text.assert_called_once_with(b"bytes")
        assert result == "Slide text"

    def test_dispatches_to_msg_by_extension(self, loader):
        loader._extract_msg_text = MagicMock(return_value="Email text")
        result = loader._extract_attachment_text(b"bytes", "application/octet-stream", "email.msg")
        loader._extract_msg_text.assert_called_once_with(b"bytes")
        assert result == "Email text"

    def test_unsupported_type_returns_empty(self, loader):
        result = loader._extract_attachment_text(b"bytes", "application/unknown", "file.xyz")
        assert result == ""


# ---------------------------------------------------------------------------
# Individual text extractors
# ---------------------------------------------------------------------------


class TestImageTextExtraction:
    def test_no_chat_model_returns_empty(self, loader):
        loader._chat_model = None
        assert loader._extract_image_text(b"img bytes") == ""

    def test_with_chat_model(self, loader):
        import sys

        mock_chat_model = MagicMock()
        loader._chat_model = mock_chat_model

        mock_processor = MagicMock()
        mock_processor.extract_text_from_image_bytes.return_value = "Extracted OCR text"
        mock_module = MagicMock()
        mock_module.ImageProcessor.return_value = mock_processor

        with patch.dict(sys.modules, {"codemie_tools.utils.image_processor": mock_module}):
            result = loader._extract_image_text(b"img bytes")

        assert result == "Extracted OCR text"

    def test_processor_exception(self, loader):
        import sys

        loader._chat_model = MagicMock()
        mock_module = MagicMock()
        mock_module.ImageProcessor.side_effect = Exception("Vision model unavailable")

        with patch.dict(sys.modules, {"codemie_tools.utils.image_processor": mock_module}):
            result = loader._extract_image_text(b"img bytes")

        assert result == ""


class TestPdfTextExtraction:
    def test_success(self, loader):
        import sys

        mock_module = MagicMock()
        mock_module.PdfProcessor.extract_text_as_markdown.return_value = "# PDF Heading\nContent"

        with patch.dict(sys.modules, {"codemie_tools.file_analysis.pdf.processor": mock_module}):
            assert loader._extract_pdf_text(b"pdf bytes") == "# PDF Heading\nContent"

    def test_error(self, loader):
        import sys

        mock_module = MagicMock()
        mock_module.PdfProcessor.extract_text_as_markdown.side_effect = Exception("Corrupt PDF")

        with patch.dict(sys.modules, {"codemie_tools.file_analysis.pdf.processor": mock_module}):
            assert loader._extract_pdf_text(b"bad bytes") == ""


class TestDocxTextExtraction:
    def test_success(self, loader):
        import sys

        mock_processor_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.text = "DOCX paragraph text"
        mock_processor_instance.read_document_from_bytes.return_value = mock_result

        mock_docx_module = MagicMock()
        mock_docx_module.DocxProcessor.return_value = mock_processor_instance
        mock_query_module = MagicMock()

        with patch.dict(
            sys.modules,
            {
                "codemie_tools.file_analysis.docx.processor": mock_docx_module,
                "codemie_tools.file_analysis.docx.models": mock_query_module,
            },
        ):
            assert loader._extract_docx_text(b"docx bytes", "file.docx") == "DOCX paragraph text"

    def test_error(self, loader):
        import sys

        mock_module = MagicMock()
        mock_module.DocxProcessor.side_effect = Exception("Not a docx")

        with patch.dict(
            sys.modules,
            {
                "codemie_tools.file_analysis.docx.processor": mock_module,
                "codemie_tools.file_analysis.docx.models": MagicMock(),
            },
        ):
            assert loader._extract_docx_text(b"bad bytes", "bad.docx") == ""


class TestXlsxTextExtraction:
    def test_success(self, loader):
        import sys

        mock_processor_instance = MagicMock()
        mock_processor_instance.load.return_value = [MagicMock()]
        mock_processor_instance.convert.return_value = "| col1 | col2 |\n|------|------|"
        mock_module = MagicMock()
        mock_module.XlsxProcessor.return_value = mock_processor_instance

        with patch.dict(sys.modules, {"codemie_tools.file_analysis.xlsx.processor": mock_module}):
            result = loader._extract_xlsx_text(b"xlsx bytes")

        assert "col1" in result

    def test_error(self, loader):
        import sys

        mock_module = MagicMock()
        mock_module.XlsxProcessor.side_effect = Exception("Bad file")

        with patch.dict(sys.modules, {"codemie_tools.file_analysis.xlsx.processor": mock_module}):
            assert loader._extract_xlsx_text(b"bad bytes") == ""


class TestPptxTextExtraction:
    def test_success(self, loader):
        import sys

        mock_pptx_doc = MagicMock()
        mock_processor_instance = MagicMock()
        mock_processor_instance.extract_text_as_markdown.return_value = "Slide 1 content"
        mock_module = MagicMock()
        mock_module.PptxProcessor.return_value = mock_processor_instance
        mock_module.PptxProcessor.open_pptx_document.return_value = mock_pptx_doc

        with patch.dict(sys.modules, {"codemie_tools.file_analysis.pptx.processor": mock_module}):
            assert loader._extract_pptx_text(b"pptx bytes") == "Slide 1 content"

    def test_error(self, loader):
        import sys

        mock_module = MagicMock()
        mock_module.PptxProcessor.side_effect = Exception("Bad PPTX")

        with patch.dict(sys.modules, {"codemie_tools.file_analysis.pptx.processor": mock_module}):
            assert loader._extract_pptx_text(b"bad bytes") == ""


class TestMsgTextExtraction:
    def test_success(self, loader):
        import sys

        mock_result = MagicMock()
        mock_result.text_content = "Email subject and body"
        mock_md_instance = MagicMock()
        mock_md_instance.convert.return_value = mock_result
        mock_module = MagicMock()
        mock_module.MarkItDown.return_value = mock_md_instance

        with patch.dict(sys.modules, {"markitdown": mock_module}):
            assert loader._extract_msg_text(b"msg bytes") == "Email subject and body"

    def test_error(self, loader):
        import sys

        mock_module = MagicMock()
        mock_module.MarkItDown.side_effect = Exception("Import failed")

        with patch.dict(sys.modules, {"markitdown": mock_module}):
            assert loader._extract_msg_text(b"bad bytes") == ""


# ---------------------------------------------------------------------------
# lazy_load integration
# ---------------------------------------------------------------------------


class TestLazyLoad:
    @patch("codemie.datasource.loader.azure_devops_work_item_loader.Connection")
    def test_yields_work_item_doc(self, mock_connection_class, loader, mock_work_item_client):
        """lazy_load yields at least the base work item Document."""
        mock_conn = MagicMock()
        mock_conn.clients_v7_1.get_work_item_tracking_client.return_value = mock_work_item_client
        mock_connection_class.return_value = mock_conn

        # WIQL returns one ref
        mock_ref = MagicMock()
        mock_ref.id = 42
        mock_wiql_result = MagicMock()
        mock_wiql_result.work_items = [mock_ref]
        mock_work_item_client.query_by_wiql.return_value = mock_wiql_result

        # Fetch work item
        mock_item = MagicMock()
        mock_item.id = 42
        mock_item.url = "https://example.com"
        mock_item.fields = {
            "System.Title": "Bug fix",
            "System.WorkItemType": "Bug",
            "System.State": "Active",
        }
        # First call → validation, second → fetch item, third → attachments expand
        mock_work_item_client.get_work_item.side_effect = [mock_item, mock_item]

        # No comments
        mock_comments_result = MagicMock()
        mock_comments_result.comments = []
        mock_work_item_client.get_comments.return_value = mock_comments_result

        # No relations (attachments)
        mock_item.relations = None

        docs = list(loader.lazy_load())

        assert len(docs) == 1
        assert docs[0].metadata["work_item_id"] == 42

    @patch("codemie.datasource.loader.azure_devops_work_item_loader.Connection")
    def test_yields_comments_doc(self, mock_connection_class, loader, mock_work_item_client):
        """lazy_load yields an extra comments Document when work item has comments."""
        mock_conn = MagicMock()
        mock_conn.clients_v7_1.get_work_item_tracking_client.return_value = mock_work_item_client
        mock_connection_class.return_value = mock_conn

        mock_ref = MagicMock()
        mock_ref.id = 42
        mock_wiql_result = MagicMock()
        mock_wiql_result.work_items = [mock_ref]
        mock_work_item_client.query_by_wiql.return_value = mock_wiql_result

        mock_item = MagicMock()
        mock_item.id = 42
        mock_item.url = "https://example.com"
        mock_item.fields = {
            "System.Title": "Task",
            "System.WorkItemType": "Task",
            "System.State": "Done",
        }
        mock_item.relations = None
        mock_work_item_client.get_work_item.side_effect = [mock_item, mock_item]

        # One comment
        mock_comment = MagicMock()
        mock_comment.as_dict.return_value = {
            "id": 1,
            "text": "Nice fix!",
            "created_by": {"display_name": "Alice"},
            "created_date": "2024-01-01",
        }
        mock_comments_result = MagicMock()
        mock_comments_result.comments = [mock_comment]
        mock_work_item_client.get_comments.return_value = mock_comments_result

        docs = list(loader.lazy_load())

        assert len(docs) == 2
        comments_doc = next(d for d in docs if d.metadata.get("content_type") == "comments")
        assert "Nice fix!" in comments_doc.page_content

    @patch("codemie.datasource.loader.azure_devops_work_item_loader.Connection")
    def test_yields_attachment_doc(self, mock_connection_class, loader, mock_work_item_client):
        """lazy_load yields an extra attachment Document when work item has attachments."""
        mock_conn = MagicMock()
        mock_conn.clients_v7_1.get_work_item_tracking_client.return_value = mock_work_item_client
        mock_connection_class.return_value = mock_conn

        mock_ref = MagicMock()
        mock_ref.id = 42
        mock_wiql_result = MagicMock()
        mock_wiql_result.work_items = [mock_ref]
        mock_work_item_client.query_by_wiql.return_value = mock_wiql_result

        mock_item_fields = MagicMock()
        mock_item_fields.id = 42
        mock_item_fields.url = "https://example.com"
        mock_item_fields.fields = {
            "System.Title": "Task",
            "System.WorkItemType": "Task",
            "System.State": "Active",
        }

        # For _get_work_item_attachments: a separate get call with expand="Relations"
        mock_item_relations = MagicMock()
        mock_item_relations.id = 42
        mock_item_relations.url = "https://example.com"
        mock_item_relations.fields = mock_item_fields.fields

        mock_rel = MagicMock()
        mock_rel.as_dict.return_value = {
            "rel": "AttachedFile",
            "url": "https://att-url",
            "attributes": {"name": "spec.pdf", "resourceSize": 1024},
        }
        mock_item_relations.relations = [mock_rel]

        mock_work_item_client.get_work_item.side_effect = [mock_item_fields, mock_item_relations]

        # No comments
        mock_comments_result = MagicMock()
        mock_comments_result.comments = []
        mock_work_item_client.get_comments.return_value = mock_comments_result

        # Mock attachment download and extraction
        loader._download_attachment = MagicMock(return_value=b"pdf bytes")
        loader._extract_attachment_text = MagicMock(return_value="Extracted PDF content")

        docs = list(loader.lazy_load())

        assert len(docs) == 2
        attachment_doc = next(d for d in docs if d.metadata.get("content_type") == "attachment")
        assert attachment_doc.metadata["attachment_name"] == "spec.pdf"
        assert attachment_doc.page_content == "Extracted PDF content"

    @patch("codemie.datasource.loader.azure_devops_work_item_loader.Connection")
    def test_attachment_error_does_not_fail_job(self, mock_connection_class, loader, mock_work_item_client):
        """Per-item attachment errors do not cause full job failure."""
        mock_conn = MagicMock()
        mock_conn.clients_v7_1.get_work_item_tracking_client.return_value = mock_work_item_client
        mock_connection_class.return_value = mock_conn

        mock_ref = MagicMock()
        mock_ref.id = 42
        mock_wiql_result = MagicMock()
        mock_wiql_result.work_items = [mock_ref]
        mock_work_item_client.query_by_wiql.return_value = mock_wiql_result

        mock_item_fields = MagicMock()
        mock_item_fields.id = 42
        mock_item_fields.url = "https://example.com"
        mock_item_fields.fields = {
            "System.Title": "Task",
            "System.WorkItemType": "Task",
            "System.State": "Active",
        }

        mock_item_relations = MagicMock()
        mock_rel = MagicMock()
        mock_rel.as_dict.return_value = {
            "rel": "AttachedFile",
            "url": "https://att-url",
            "attributes": {"name": "bad.pdf"},
        }
        mock_item_relations.relations = [mock_rel]
        mock_work_item_client.get_work_item.side_effect = [mock_item_fields, mock_item_relations]

        mock_comments_result = MagicMock()
        mock_comments_result.comments = []
        mock_work_item_client.get_comments.return_value = mock_comments_result

        # Attachment processing raises
        loader._download_attachment = MagicMock(side_effect=Exception("Boom"))

        # Should still yield the work item doc without crashing
        docs = list(loader.lazy_load())

        assert len(docs) == 1
        assert docs[0].metadata["work_item_id"] == 42


# ---------------------------------------------------------------------------
# fetch_remote_stats
# ---------------------------------------------------------------------------


class TestFetchRemoteStats:
    @patch("codemie.datasource.loader.azure_devops_work_item_loader.Connection")
    def test_success(self, mock_connection_class, loader, mock_work_item_client):
        mock_conn = MagicMock()
        mock_conn.clients_v7_1.get_work_item_tracking_client.return_value = mock_work_item_client
        mock_connection_class.return_value = mock_conn

        mock_wiql_result = MagicMock()
        mock_wiql_result.work_items = [MagicMock(), MagicMock(), MagicMock()]
        mock_work_item_client.query_by_wiql.return_value = mock_wiql_result

        stats = loader.fetch_remote_stats()

        assert stats[AzureDevOpsWorkItemLoader.DOCUMENTS_COUNT_KEY] == 3
        assert stats[AzureDevOpsWorkItemLoader.TOTAL_DOCUMENTS_KEY] == 3
        assert stats[AzureDevOpsWorkItemLoader.SKIPPED_DOCUMENTS_KEY] == 0

    @patch("codemie.datasource.loader.azure_devops_work_item_loader.Connection")
    def test_error_returns_zero(self, mock_connection_class, loader, mock_work_item_client):
        mock_conn = MagicMock()
        mock_conn.clients_v7_1.get_work_item_tracking_client.return_value = mock_work_item_client
        mock_connection_class.return_value = mock_conn

        # First call for validation succeeds, second for stats fails
        mock_wiql_result = MagicMock()
        mock_wiql_result.work_items = []
        mock_work_item_client.query_by_wiql.side_effect = [
            mock_wiql_result,  # validation
            Exception("API error"),  # stats
        ]

        stats = loader.fetch_remote_stats()

        assert stats[AzureDevOpsWorkItemLoader.DOCUMENTS_COUNT_KEY] == 0


# ---------------------------------------------------------------------------
# _create_stats_response
# ---------------------------------------------------------------------------


class TestCreateStatsResponse:
    def test_creates_correct_response(self, loader):
        stats = loader._create_stats_response(10)

        assert stats[AzureDevOpsWorkItemLoader.DOCUMENTS_COUNT_KEY] == 10
        assert stats[AzureDevOpsWorkItemLoader.TOTAL_DOCUMENTS_KEY] == 10
        assert stats[AzureDevOpsWorkItemLoader.SKIPPED_DOCUMENTS_KEY] == 0
