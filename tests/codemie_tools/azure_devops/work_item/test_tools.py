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

import base64
import hashlib
from unittest.mock import Mock, MagicMock, patch

import pytest

from codemie_tools.azure_devops.work_item.models import AzureDevOpsWorkItemConfig
from codemie_tools.azure_devops.work_item.tools import (
    SearchWorkItemsTool,
    CreateWorkItemTool,
    UpdateWorkItemTool,
    GetWorkItemTool,
    GetRelationTypesTool,
    RemoveWorkItemRelationTool,
    MoveWorkItemTool,
    CreateCommentTool,
    GetWorkItemAttachmentContentTool,
    _HIERARCHY_REVERSE,
)

_WI_JSON_STUB = '{"fields": {"System.Title": "T"}}'
_NEW_PDF = "new.pdf"
_UPLOAD_ATTACHMENT = "_upload_attachment"
_DEFAULT_CONTENT = b"file content"
_DEFAULT_CONTENT_HASH = hashlib.sha256(_DEFAULT_CONTENT).hexdigest()


@pytest.fixture
def mock_config():
    return AzureDevOpsWorkItemConfig(
        organization_url="https://dev.azure.com/org", project="test-project", token="fake-token", limit=5
    )


@pytest.fixture
def mock_client():
    return Mock()


@pytest.fixture
def search_tool(mock_config, mock_client):
    tool = SearchWorkItemsTool(config=mock_config)
    tool._client = mock_client
    return tool


@pytest.fixture
def create_tool(mock_config, mock_client):
    tool = CreateWorkItemTool(config=mock_config)
    tool._client = mock_client
    return tool


@pytest.fixture
def update_tool(mock_config, mock_client):
    tool = UpdateWorkItemTool(config=mock_config)
    tool._client = mock_client
    return tool


@pytest.fixture
def get_tool(mock_config, mock_client):
    tool = GetWorkItemTool(config=mock_config)
    tool._client = mock_client
    return tool


class TestSearchWorkItemsTool:
    def test_search_work_items_success(self, search_tool, mock_client):
        # Arrange
        query = "SELECT [System.Id] FROM WorkItems"
        mock_work_item = MagicMock()
        mock_work_item.id = 1
        mock_client.query_by_wiql.return_value.work_items = [mock_work_item]

        mock_full_item = MagicMock()
        mock_full_item.id = 1
        mock_full_item.fields = {"System.Title": "Test Item"}
        mock_client.get_work_item.return_value = mock_full_item

        # Act
        result = search_tool.execute(query=query)

        # Assert
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == 1
        mock_client.query_by_wiql.assert_called_once()

    def test_search_work_items_no_results(self, search_tool, mock_client):
        # Arrange
        query = "SELECT [System.Id] FROM WorkItems"
        mock_client.query_by_wiql.return_value.work_items = []

        # Act
        result = search_tool.execute(query=query)

        # Assert
        assert result == "No work items found."


class TestCreateWorkItemTool:
    def test_create_work_item_success(self, create_tool, mock_client):
        # Arrange
        work_item_json = '{"fields": {"System.Title": "Test Item"}}'
        mock_response = MagicMock()
        mock_response.id = 1
        mock_response.url = "http://test-url"
        mock_client.create_work_item.return_value = mock_response

        # Act
        result = create_tool.execute(work_item_json=work_item_json)

        # Assert
        assert "created successfully" in result
        assert "1" in result
        mock_client.create_work_item.assert_called_once()

    def test_create_attaches_file_when_work_item_has_no_prior_relations(self, create_tool, mock_client):
        """Newly created work item has no existing relations — file must be uploaded normally."""
        mock_response = Mock()
        mock_response.id = 99
        mock_response.url = "http://test-url"
        mock_client.create_work_item.return_value = mock_response

        existing_wi = Mock()
        existing_wi.relations = None
        mock_client.get_work_item.return_value = existing_wi

        upload_url = "https://dev.azure.com/org/_apis/wit/attachments/eee"
        create_tool.config.input_files = [_make_file_object("spec.pdf", b"spec content")]

        with patch.object(create_tool, _UPLOAD_ATTACHMENT, return_value=upload_url) as mock_upload:
            result = create_tool.execute(work_item_json='{"fields": {"System.Title": "New"}}')

        mock_upload.assert_called_once_with("spec.pdf", b"spec content")
        assert "created successfully" in result
        assert "spec.pdf" in result


class TestUpdateWorkItemTool:
    def test_update_work_item_success(self, update_tool, mock_client):
        # Arrange
        work_item_json = '{"fields": {"System.Title": "Updated Title"}}'
        mock_response = MagicMock()
        mock_response.id = 1
        mock_client.update_work_item.return_value = mock_response

        # Act
        result = update_tool.execute(id=1, work_item_json=work_item_json)

        # Assert
        assert "was updated" in result
        mock_client.update_work_item.assert_called_once()

    def test_update_does_not_reattach_file_already_in_relations(self, update_tool, mock_client):
        """Same file present in existing AttachedFile relations must not be re-uploaded."""
        work_item_id = 42
        mock_response = Mock()
        mock_response.id = work_item_id
        mock_client.update_work_item.return_value = mock_response

        existing_rel = _make_relation(
            "AttachedFile",
            "doc.pdf",
            "https://dev.azure.com/org/_apis/wit/attachments/aaa",
            comment=f"Attached file: doc.pdf [sha256:{_DEFAULT_CONTENT_HASH}]",
        )
        existing_wi = Mock()
        existing_wi.relations = [existing_rel]
        mock_client.get_work_item.return_value = existing_wi

        update_tool.config.input_files = [_make_file_object("doc.pdf")]

        with patch.object(update_tool, _UPLOAD_ATTACHMENT) as mock_upload:
            result = update_tool.execute(id=work_item_id, work_item_json=_WI_JSON_STUB)

        mock_upload.assert_not_called()
        assert "was updated" in result

    def test_update_attaches_new_file_not_yet_in_relations(self, update_tool, mock_client):
        """File with a name not in existing relations must still be uploaded and attached."""
        work_item_id = 42
        mock_response = Mock()
        mock_response.id = work_item_id
        mock_client.update_work_item.return_value = mock_response

        existing_rel = _make_relation("AttachedFile", "old.pdf", "https://dev.azure.com/org/_apis/wit/attachments/bbb")
        existing_wi = Mock()
        existing_wi.relations = [existing_rel]
        mock_client.get_work_item.return_value = existing_wi

        upload_url = "https://dev.azure.com/org/_apis/wit/attachments/ccc"
        update_tool.config.input_files = [_make_file_object(_NEW_PDF, b"new content")]

        with patch.object(update_tool, _UPLOAD_ATTACHMENT, return_value=upload_url) as mock_upload:
            result = update_tool.execute(id=work_item_id, work_item_json=_WI_JSON_STUB)

        mock_upload.assert_called_once_with(_NEW_PDF, b"new content")
        assert _NEW_PDF in result

    def test_update_with_no_files_does_not_call_get_work_item(self, update_tool, mock_client):
        """When config.input_files is empty, get_work_item must not be called."""
        mock_response = Mock()
        mock_response.id = 1
        mock_client.update_work_item.return_value = mock_response
        update_tool.config.input_files = []

        update_tool.execute(id=1, work_item_json='{"fields": {"System.Title": "No files"}}')

        mock_client.get_work_item.assert_not_called()

    def test_update_dedup_is_case_insensitive(self, update_tool, mock_client):
        """Filename comparison must be case-insensitive."""
        work_item_id = 5
        mock_response = Mock()
        mock_response.id = work_item_id
        mock_client.update_work_item.return_value = mock_response

        existing_rel = _make_relation(
            "AttachedFile",
            "REPORT.PDF",
            "https://dev.azure.com/org/_apis/wit/attachments/ddd",
            comment=f"Attached file: REPORT.PDF [sha256:{_DEFAULT_CONTENT_HASH}]",
        )
        existing_wi = Mock()
        existing_wi.relations = [existing_rel]
        mock_client.get_work_item.return_value = existing_wi

        update_tool.config.input_files = [_make_file_object("report.pdf")]

        with patch.object(update_tool, _UPLOAD_ATTACHMENT) as mock_upload:
            update_tool.execute(id=work_item_id, work_item_json=_WI_JSON_STUB)

        mock_upload.assert_not_called()

    def test_update_skips_file_on_second_call(self, update_tool, mock_client):
        """Consecutive-update round-trip: relation written by _process_attachments (name + comment) must be recognised and skipped."""
        work_item_id = 7
        mock_response = Mock()
        mock_response.id = work_item_id
        mock_client.update_work_item.return_value = mock_response

        existing_rel = _make_relation(
            "AttachedFile",
            "spec.pdf",
            "https://dev.azure.com/org/_apis/wit/attachments/fff",
            comment=f"Attached file: spec.pdf [sha256:{_DEFAULT_CONTENT_HASH}]",
        )
        existing_wi = Mock()
        existing_wi.relations = [existing_rel]
        mock_client.get_work_item.return_value = existing_wi

        update_tool.config.input_files = [_make_file_object("spec.pdf")]

        with patch.object(update_tool, _UPLOAD_ATTACHMENT) as mock_upload:
            update_tool.execute(id=work_item_id, work_item_json=_WI_JSON_STUB)

        mock_upload.assert_not_called()

    def test_update_with_mixed_files_attaches_only_new(self, update_tool, mock_client):
        """When one file is already attached and one is new, only the new file must be uploaded."""
        work_item_id = 8
        mock_response = Mock()
        mock_response.id = work_item_id
        mock_client.update_work_item.return_value = mock_response

        existing_rel = _make_relation(
            "AttachedFile",
            "existing.pdf",
            "https://dev.azure.com/org/_apis/wit/attachments/ggg",
            comment=f"Attached file: existing.pdf [sha256:{_DEFAULT_CONTENT_HASH}]",
        )
        existing_wi = Mock()
        existing_wi.relations = [existing_rel]
        mock_client.get_work_item.return_value = existing_wi

        upload_url = "https://dev.azure.com/org/_apis/wit/attachments/hhh"
        update_tool.config.input_files = [
            _make_file_object("existing.pdf"),
            _make_file_object(_NEW_PDF, b"new bytes"),
        ]

        with patch.object(update_tool, _UPLOAD_ATTACHMENT, return_value=upload_url) as mock_upload:
            result = update_tool.execute(id=work_item_id, work_item_json=_WI_JSON_STUB)

        mock_upload.assert_called_once_with(_NEW_PDF, b"new bytes")
        assert _NEW_PDF in result

    def test_update_reattaches_revised_file_with_same_name(self, update_tool, mock_client):
        """A file whose content changed (different hash) must be re-attached even if its name is already present."""
        work_item_id = 9
        mock_response = Mock()
        mock_response.id = work_item_id
        mock_client.update_work_item.return_value = mock_response

        old_hash = hashlib.sha256(b"old content").hexdigest()
        existing_rel = _make_relation(
            "AttachedFile",
            "report.pdf",
            "https://dev.azure.com/org/_apis/wit/attachments/iii",
            comment=f"Attached file: report.pdf [sha256:{old_hash}]",
        )
        existing_wi = Mock()
        existing_wi.relations = [existing_rel]
        mock_client.get_work_item.return_value = existing_wi

        upload_url = "https://dev.azure.com/org/_apis/wit/attachments/jjj"
        update_tool.config.input_files = [_make_file_object("report.pdf", b"revised content")]

        with patch.object(update_tool, _UPLOAD_ATTACHMENT, return_value=upload_url) as mock_upload:
            update_tool.execute(id=work_item_id, work_item_json=_WI_JSON_STUB)

        mock_upload.assert_called_once_with("report.pdf", b"revised content")


class TestGetWorkItemTool:
    def test_get_work_item_success(self, get_tool, mock_client):
        # Arrange
        mock_work_item = MagicMock()
        mock_work_item.id = 1
        mock_work_item.fields = {"System.Title": "Test Item"}
        mock_work_item.relations = []
        mock_client.get_work_item.return_value = mock_work_item

        # Act
        result = get_tool.execute(id=1)

        # Assert
        assert isinstance(result, dict)
        assert result["id"] == 1
        assert "System.Title" in result
        mock_client.get_work_item.assert_called_once()


class TestCreateCommentTool:
    @pytest.fixture
    def create_comment_tool(self, mock_config, mock_client):
        tool = CreateCommentTool(config=mock_config)
        tool._client = mock_client
        return tool

    def test_create_comment_success(self, create_comment_tool, mock_client):
        mock_comment = MagicMock()
        mock_comment.id = 42
        mock_client.add_comment.return_value = mock_comment

        result = create_comment_tool.execute(work_item_id=123, text="Test comment")

        assert "42" in result
        assert "123" in result
        mock_client.add_comment.assert_called_once()

    def test_create_comment_api_error(self, create_comment_tool, mock_client):
        from langchain_core.tools import ToolException

        mock_client.add_comment.side_effect = Exception("API error")

        with pytest.raises(ToolException, match="Error creating comment"):
            create_comment_tool.execute(work_item_id=123, text="Test comment")


class TestGetRelationTypesTool:
    def test_get_relation_types_success(self, mock_config, mock_client):
        # Arrange
        tool = GetRelationTypesTool(config=mock_config)
        tool._client = mock_client

        mock_relation = MagicMock()
        mock_relation.name = "Relates"
        mock_relation.reference_name = "System.LinkTypes.Related"
        mock_client.get_relation_types.return_value = [mock_relation]

        # Act
        result = tool.execute()

        # Assert
        assert isinstance(result, dict)
        assert "Relates" in result
        assert result["Relates"] == "System.LinkTypes.Related"


def _make_relation(rel_type: str, name: str, url: str, comment: str = None):
    """Build a mock work item relation with as_dict() support."""
    rel = Mock()
    attrs = {"name": name}
    if comment:
        attrs["comment"] = comment
    rel.as_dict.return_value = {"rel": rel_type, "url": url, "attributes": attrs}
    return rel


class TestGetWorkItemAttachmentContentTool:
    @pytest.fixture
    def mock_config(self):
        return AzureDevOpsWorkItemConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )

    @pytest.fixture
    def tool(self, mock_config):
        t = GetWorkItemAttachmentContentTool(config=mock_config)
        t._client = Mock()
        return t

    # ------------------------------------------------------------------ mixin

    def test_build_base64_response_small_file(self, tool):
        data = b"small content"
        result = tool._build_base64_response(data, "note")
        assert result["content_type"] == "base64"
        assert result["content"] == base64.b64encode(data).decode()
        assert result["note"] == "note"

    def test_build_base64_response_large_file_returns_metadata_only(self, tool):
        data = b"x" * (tool._MAX_BASE64_BYTES + 1)
        result = tool._build_base64_response(data, "original note")
        assert result["content_type"] == "metadata_only"
        assert result["content"] is None
        assert "too large" in result["note"]

    def test_detect_mime_type_known_extension(self, tool):
        assert tool._detect_mime_type("report.pdf") == "application/pdf"
        assert tool._detect_mime_type("image.png") == "image/png"

    def test_detect_mime_type_unknown_returns_octet_stream(self, tool):
        assert tool._detect_mime_type("file.unknownxyz") == "application/octet-stream"

    def test_is_text_based_text_mime(self, tool):
        assert tool._is_text_based("text/plain", "file.dat") is True

    def test_is_text_based_known_text_extension(self, tool):
        assert tool._is_text_based("application/octet-stream", "notes.md") is True

    def test_is_text_based_binary(self, tool):
        assert tool._is_text_based("application/octet-stream", "binary.exe") is False

    def test_process_content_text_file(self, tool):
        result = tool._process_content("readme.txt", b"Hello world")
        assert result["content_type"] == "text"
        assert result["content"] == "Hello world"

    def test_process_content_unknown_large_returns_metadata_only(self, tool):
        data = b"x" * (tool._MAX_BASE64_BYTES + 1)
        result = tool._process_content("blob.bin", data)
        assert result["content_type"] == "metadata_only"
        assert result["content"] is None

    def test_process_content_unknown_small_returns_base64(self, tool):
        data = b"x" * 100
        result = tool._process_content("blob.bin", data)
        assert result["content_type"] == "base64"

    def test_process_content_xlsb_extension_fallback_when_mime_unknown(self, tool):
        """In a slim Linux container without /etc/mime.types, guess_type returns None for
        .xlsb; the extension must still route to Excel extraction rather than base64
        (EPMCDME-11738 follow-up item 3)."""
        import pandas as pd

        df = pd.DataFrame({"A": ["cell"]})
        with patch("mimetypes.guess_type", return_value=(None, None)):
            with patch("codemie_tools.azure_devops.attachment_content_mixin.XlsxProcessor") as mock_proc:
                instance = mock_proc.return_value
                instance.load.return_value = {"Sheet1": df}
                instance.convert.return_value = "## Sheet1\ncell"
                result = tool._process_content("report.xlsb", b"fake xlsb")
        assert result["content_type"] == "text"
        assert "cell" in result["content"]
        assert instance.load.call_args.kwargs.get("file_ext") == ".xlsb"

    def test_process_content_xls_extension_fallback_when_mime_unknown(self, tool):
        """.xls extension fallback (guess_type None) must also reach Excel extraction."""
        import pandas as pd

        df = pd.DataFrame({"A": ["v"]})
        with patch("mimetypes.guess_type", return_value=(None, None)):
            with patch("codemie_tools.azure_devops.attachment_content_mixin.XlsxProcessor") as mock_proc:
                instance = mock_proc.return_value
                instance.load.return_value = {"Sheet1": df}
                instance.convert.return_value = "content"
                result = tool._process_content("legacy.xls", b"fake xls")
        assert result["content_type"] == "text"

    # ------------------------------------------------ _find_attachment_in_relations

    def test_find_attachment_success(self, tool):
        attachment = _make_relation(
            "AttachedFile",
            "spec.pdf",
            "https://dev.azure.com/org/proj/_apis/wit/attachments/abc-123",
            comment="Initial spec",
        )
        work_item = Mock()
        work_item.relations = [attachment]
        tool._client.get_work_item.return_value = work_item

        filename, url, note = tool._find_attachment_in_relations(42, "spec.pdf")

        assert filename == "spec.pdf"
        assert "abc-123" in url
        assert note == "Initial spec"
        tool._client.get_work_item.assert_called_once_with(id=42, project="test-project", expand="Relations")

    def test_find_attachment_case_insensitive(self, tool):
        attachment = _make_relation(
            "AttachedFile",
            "SPEC.PDF",
            "https://dev.azure.com/org/proj/_apis/wit/attachments/abc",
        )
        work_item = Mock()
        work_item.relations = [attachment]
        tool._client.get_work_item.return_value = work_item

        filename, url, note = tool._find_attachment_in_relations(42, "spec.pdf")
        assert filename == "SPEC.PDF"

    def test_find_attachment_skips_non_attached_file_relations(self, tool):
        link_rel = _make_relation("System.LinkTypes.Related", "ignored", "https://example.com/rel")
        work_item = Mock()
        work_item.relations = [link_rel]
        tool._client.get_work_item.return_value = work_item

        from langchain_core.tools import ToolException

        with pytest.raises(ToolException, match="has no file attachments"):
            tool._find_attachment_in_relations(42, "spec.pdf")

    def test_find_attachment_no_relations_raises(self, tool):
        from langchain_core.tools import ToolException

        work_item = Mock()
        work_item.relations = None
        tool._client.get_work_item.return_value = work_item

        with pytest.raises(ToolException, match="has no file attachments"):
            tool._find_attachment_in_relations(42, "spec.pdf")

    def test_find_attachment_not_found_lists_available(self, tool):
        from langchain_core.tools import ToolException

        attachment = _make_relation("AttachedFile", "other.docx", "https://example.com/att")
        work_item = Mock()
        work_item.relations = [attachment]
        tool._client.get_work_item.return_value = work_item

        with pytest.raises(ToolException, match="other.docx"):
            tool._find_attachment_in_relations(42, "missing.pdf")

    # ----------------------------------------------------------- execute

    def test_execute_with_direct_url_extracts_filename_from_query(self, tool):
        with patch.object(tool, "_download_attachment", return_value=b"hello world"):
            result = tool.execute(
                work_item_id=42,
                attachment_url="https://dev.azure.com/org/proj/_apis/wit/attachments/abc?fileName=notes.txt",
            )

        assert result["work_item_id"] == 42
        assert result["filename"] == "notes.txt"
        assert result["content_type"] == "text"
        assert result["content"] == "hello world"
        assert result["size_bytes"] == 11

    def test_execute_with_direct_url_and_explicit_name(self, tool):
        with patch.object(tool, "_download_attachment", return_value=b"data"):
            result = tool.execute(
                work_item_id=7,
                attachment_url="https://dev.azure.com/org/proj/_apis/wit/attachments/xyz",
                attachment_name="override.txt",
            )

        assert result["filename"] == "override.txt"

    def test_execute_by_name_discovery(self, tool):
        url = "https://dev.azure.com/org/proj/_apis/wit/attachments/abc-123"
        with patch.object(tool, "_find_attachment_in_relations", return_value=("spec.pdf", url, "draft spec")):
            with patch.object(tool, "_download_attachment", return_value=b"PDF content"):
                result = tool.execute(work_item_id=99, attachment_name="spec.pdf")

        assert result["filename"] == "spec.pdf"
        assert result["attachment_note"] == "draft spec"
        assert result["work_item_id"] == 99

    def test_execute_missing_both_url_and_name_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="attachment_url.*attachment_name"):
            from codemie_tools.azure_devops.work_item.models import GetWorkItemAttachmentContentInput

            GetWorkItemAttachmentContentInput(work_item_id=1)

    def test_execute_download_failure_wraps_in_tool_exception(self, tool):
        from langchain_core.tools import ToolException

        with patch.object(tool, "_download_attachment", side_effect=Exception("Network timeout")):
            with pytest.raises(ToolException, match="Failed to retrieve attachment content"):
                tool.execute(
                    work_item_id=42,
                    attachment_url="https://dev.azure.com/org/proj/_apis/wit/attachments/abc",
                )

    def test_execute_returns_full_response_structure(self, tool):
        url = "https://dev.azure.com/org/proj/_apis/wit/attachments/abc?fileName=data.json"
        with patch.object(tool, "_download_attachment", return_value=b'{"key": "value"}'):
            result = tool.execute(work_item_id=5, attachment_url=url)

        assert set(result.keys()) == {
            "work_item_id",
            "filename",
            "attachment_note",
            "mime_type",
            "size_bytes",
            "content_type",
            "content",
            "note",
        }
        assert result["attachment_note"] is None


def _make_file_object(name: str, content: bytes = b"file content", mime_type: str = "application/octet-stream"):
    """Build a mock FileObject for injection into config.input_files."""
    fo = Mock()
    fo.name = name
    fo.mime_type = mime_type
    fo.bytes_content.return_value = content
    return fo


def _make_hierarchy_relation(rel_type: str, url: str):
    """Build a mock work item relation object (not as_dict-based)."""
    rel = Mock()
    rel.rel = rel_type
    rel.url = url
    return rel


class TestRemoveWorkItemRelationTool:
    @pytest.fixture
    def tool(self, mock_config, mock_client):
        t = RemoveWorkItemRelationTool(config=mock_config)
        t._client = mock_client
        return t

    def test_remove_relation_success(self, tool, mock_client):
        result = tool.execute(work_item_id=100, relation_index=2)

        assert "100" in result
        assert "2" in result
        mock_client.update_work_item.assert_called_once_with(
            document=[{"op": "remove", "path": "/relations/2"}],
            id=100,
        )

    def test_remove_relation_index_zero(self, tool, mock_client):
        result = tool.execute(work_item_id=42, relation_index=0)

        mock_client.update_work_item.assert_called_once_with(
            document=[{"op": "remove", "path": "/relations/0"}],
            id=42,
        )
        assert "0" in result

    def test_remove_relation_api_error_raises_tool_exception(self, tool, mock_client):
        from langchain_core.tools import ToolException

        mock_client.update_work_item.side_effect = Exception("ADO API error")

        with pytest.raises(ToolException, match="Error removing work item relation"):
            tool.execute(work_item_id=100, relation_index=1)


class TestMoveWorkItemTool:
    ORG_URL = "https://dev.azure.com/org"

    @pytest.fixture
    def tool(self, mock_client):
        config = AzureDevOpsWorkItemConfig(organization_url=self.ORG_URL, project="test-project", token="fake-token")
        t = MoveWorkItemTool(config=config)
        t._client = mock_client
        return t

    def _work_item_with_relations(self, relations):
        wi = Mock()
        wi.relations = relations
        return wi

    def test_move_with_existing_parent_removes_old_and_adds_new(self, tool, mock_client):
        """Patch document must include remove op for old parent then add op for new parent."""
        old_parent_rel = _make_hierarchy_relation(
            _HIERARCHY_REVERSE,
            f"{self.ORG_URL}/_apis/wit/workItems/1005",
        )
        initial_wi = self._work_item_with_relations([old_parent_rel])

        new_parent_rel = _make_hierarchy_relation(
            _HIERARCHY_REVERSE,
            f"{self.ORG_URL}/_apis/wit/workItems/1009",
        )
        updated_wi = self._work_item_with_relations([new_parent_rel])

        mock_client.get_work_item.return_value = initial_wi
        mock_client.update_work_item.return_value = updated_wi

        result = tool.execute(work_item_id=1139, new_parent_id=1009)

        assert "1139" in result
        assert "1009" in result

        patch_doc = mock_client.update_work_item.call_args.kwargs["document"]
        assert patch_doc[0] == {"op": "remove", "path": "/relations/0"}
        assert patch_doc[1]["op"] == "add"
        assert patch_doc[1]["value"]["rel"] == _HIERARCHY_REVERSE
        assert "1009" in patch_doc[1]["value"]["url"]

    def test_move_without_existing_parent_only_adds_new(self, tool, mock_client):
        """When work item has no parent, patch document must contain only the add op."""
        unrelated_rel = _make_hierarchy_relation("System.LinkTypes.Related", f"{self.ORG_URL}/_apis/wit/workItems/999")
        initial_wi = self._work_item_with_relations([unrelated_rel])

        new_parent_rel = _make_hierarchy_relation(
            _HIERARCHY_REVERSE,
            f"{self.ORG_URL}/_apis/wit/workItems/1009",
        )
        updated_wi = self._work_item_with_relations([new_parent_rel])

        mock_client.get_work_item.return_value = initial_wi
        mock_client.update_work_item.return_value = updated_wi

        tool.execute(work_item_id=1139, new_parent_id=1009)

        patch_doc = mock_client.update_work_item.call_args.kwargs["document"]
        assert len(patch_doc) == 1
        assert patch_doc[0]["op"] == "add"

    def test_move_work_item_with_no_relations_at_all(self, tool, mock_client):
        """work_item.relations == None — no remove op, only add."""
        initial_wi = self._work_item_with_relations(None)

        new_parent_rel = _make_hierarchy_relation(
            _HIERARCHY_REVERSE,
            f"{self.ORG_URL}/_apis/wit/workItems/1009",
        )
        updated_wi = self._work_item_with_relations([new_parent_rel])

        mock_client.get_work_item.return_value = initial_wi
        mock_client.update_work_item.return_value = updated_wi

        tool.execute(work_item_id=1139, new_parent_id=1009)

        patch_doc = mock_client.update_work_item.call_args.kwargs["document"]
        assert len(patch_doc) == 1
        assert patch_doc[0]["op"] == "add"

    def test_move_parent_at_non_zero_index(self, tool, mock_client):
        """Parent relation at index 1 must produce remove path /relations/1."""
        attachment_rel = _make_hierarchy_relation("AttachedFile", "https://example.com/att")
        parent_rel = _make_hierarchy_relation(
            _HIERARCHY_REVERSE,
            f"{self.ORG_URL}/_apis/wit/workItems/1005",
        )
        initial_wi = self._work_item_with_relations([attachment_rel, parent_rel])

        new_parent_rel = _make_hierarchy_relation(
            _HIERARCHY_REVERSE,
            f"{self.ORG_URL}/_apis/wit/workItems/1009",
        )
        updated_wi = self._work_item_with_relations([attachment_rel, new_parent_rel])

        mock_client.get_work_item.return_value = initial_wi
        mock_client.update_work_item.return_value = updated_wi

        tool.execute(work_item_id=1139, new_parent_id=1009)

        patch_doc = mock_client.update_work_item.call_args.kwargs["document"]
        assert patch_doc[0] == {"op": "remove", "path": "/relations/1"}

    def test_move_url_uses_endswith_not_substring_match(self, tool, mock_client):
        """Verification must not confuse parent 100 with parent 1009 (substring false positive)."""
        initial_wi = self._work_item_with_relations(None)

        # Verification: rel points to 1009, new_parent_id is 100 — should NOT match
        wrong_rel = _make_hierarchy_relation(
            _HIERARCHY_REVERSE,
            f"{self.ORG_URL}/_apis/wit/workItems/1009",
        )
        updated_wi = self._work_item_with_relations([wrong_rel])

        mock_client.get_work_item.return_value = initial_wi
        mock_client.update_work_item.return_value = updated_wi

        from langchain_core.tools import ToolException

        with pytest.raises(ToolException, match="verification failed"):
            tool.execute(work_item_id=1139, new_parent_id=100)

    def test_move_verification_failure_raises_tool_exception(self, tool, mock_client):
        """If updated relations don't contain the new parent, ToolException is raised."""
        initial_wi = self._work_item_with_relations(None)
        # Verification returns item with no relations at all
        updated_wi = self._work_item_with_relations(None)

        mock_client.get_work_item.return_value = initial_wi
        mock_client.update_work_item.return_value = updated_wi

        from langchain_core.tools import ToolException

        with pytest.raises(ToolException, match="verification failed"):
            tool.execute(work_item_id=1139, new_parent_id=1009)

    def test_move_get_work_item_api_error_raises_tool_exception(self, tool, mock_client):
        mock_client.get_work_item.side_effect = Exception("Connection refused")

        from langchain_core.tools import ToolException

        with pytest.raises(ToolException, match="Error moving work item"):
            tool.execute(work_item_id=1139, new_parent_id=1009)

    def test_move_update_work_item_api_error_raises_tool_exception(self, tool, mock_client):
        initial_wi = self._work_item_with_relations(None)
        mock_client.get_work_item.return_value = initial_wi
        mock_client.update_work_item.side_effect = Exception("TF201036: cannot add Child link")

        from langchain_core.tools import ToolException

        with pytest.raises(ToolException, match="Error moving work item"):
            tool.execute(work_item_id=1139, new_parent_id=1009)

    def test_move_new_parent_url_is_correct(self, tool, mock_client):
        """The new parent relation URL must point to the correct organization URL and ID."""
        initial_wi = self._work_item_with_relations(None)
        new_parent_rel = _make_hierarchy_relation(
            _HIERARCHY_REVERSE,
            f"{self.ORG_URL}/_apis/wit/workItems/1009",
        )
        updated_wi = self._work_item_with_relations([new_parent_rel])
        mock_client.get_work_item.return_value = initial_wi
        mock_client.update_work_item.return_value = updated_wi

        tool.execute(work_item_id=1139, new_parent_id=1009)

        patch_doc = mock_client.update_work_item.call_args.kwargs["document"]
        add_op = patch_doc[-1]
        assert add_op["value"]["url"] == f"{self.ORG_URL}/_apis/wit/workItems/1009"
        assert add_op["value"]["rel"] == _HIERARCHY_REVERSE
