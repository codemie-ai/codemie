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

"""Unit tests for SearchKBTool — image artifact pipeline and datasource health."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.tools import ToolException

from codemie.agents.tools.kb.search_kb import SearchKBResponse, SearchKBTool
from codemie.rest_api.models.index import IndexInfo, IndexInfoType
from codemie.service.constants import FullDatasourceTypes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FILE_SERVICE_PATH = "codemie.agents.tools.kb.search_kb.FileService.get_image_base64"


def _make_tool(
    index_type: str = "kb",
    error: bool = False,
    completed: bool = True,
    last_reindex_triggered_at=None,
    update_date=None,
    repo_name: str = "test-kb",
    text=None,
) -> SearchKBTool:
    kb_index = MagicMock(spec=IndexInfo)
    kb_index.index_type = index_type
    kb_index.repo_name = repo_name
    kb_index.description = "test description"
    kb_index.error = error
    kb_index.completed = completed
    kb_index.last_reindex_triggered_at = last_reindex_triggered_at
    kb_index.update_date = update_date
    kb_index.text = text
    return SearchKBTool.model_construct(
        index_info=kb_index,
        llm_model="gpt-4",
        metadata={},
        tokens_size_limit=20000,
    )


def _make_doc(
    page_content: str = "content",
    *,
    with_image: bool = False,
    mime_type: str = "image/png",
    encoded_url: str = "encoded-url-abc",
) -> Document:
    """Create a Document, optionally with image_encoded_url metadata."""
    metadata: dict = {"source": "file.txt"}
    if with_image:
        metadata["image_encoded_url"] = encoded_url
        metadata["image_mime_type"] = mime_type
    return Document(page_content=page_content, metadata=metadata)


# ---------------------------------------------------------------------------
# _collect_image_artifacts
# ---------------------------------------------------------------------------


class TestCollectImageArtifacts(unittest.TestCase):
    """Tests for SearchKBTool._collect_image_artifacts."""

    def setUp(self):
        self.tool = _make_tool()

    # --- plain list inputs ---

    def test_returns_empty_list_when_no_image_metadata(self):
        result = self.tool._collect_image_artifacts([_make_doc(), _make_doc()])
        self.assertEqual(result, [])

    def test_collects_artifact_for_image_doc(self):
        doc = _make_doc(with_image=True, mime_type="image/jpeg", encoded_url="encoded-url-jpeg")
        with patch(_FILE_SERVICE_PATH, return_value="abc123==") as mock_get:
            result = self.tool._collect_image_artifacts([doc])
        mock_get.assert_called_once_with("encoded-url-jpeg")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["data"], "abc123==")
        self.assertEqual(result[0]["mime_type"], "image/jpeg")

    def test_skips_artifact_on_fileservice_error(self):
        doc = _make_doc(with_image=True, mime_type="image/png", encoded_url="bad-url")
        with patch(_FILE_SERVICE_PATH, side_effect=Exception("fetch failed")):
            result = self.tool._collect_image_artifacts([doc])
        self.assertEqual(result, [])

    def test_returns_only_docs_that_have_image_encoded_url(self):
        docs = [
            _make_doc(with_image=True, mime_type="image/png", encoded_url="url-png"),
            _make_doc(),
            _make_doc(with_image=True, mime_type="image/gif", encoded_url="url-gif"),
        ]
        with patch(_FILE_SERVICE_PATH, side_effect=["png-b64", "gif-b64"]):
            result = self.tool._collect_image_artifacts(docs)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["mime_type"], "image/png")
        self.assertEqual(result[1]["mime_type"], "image/gif")

    def test_skips_non_document_items_in_list(self):
        doc = _make_doc(with_image=True, encoded_url="url-abc")
        non_doc = {"page_content": "text", "metadata": {"image_encoded_url": "xyz", "image_mime_type": "image/png"}}
        with patch(_FILE_SERVICE_PATH, return_value="abc123=="):
            result = self.tool._collect_image_artifacts([doc, non_doc])  # type: ignore[arg-type]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["data"], "abc123==")

    def test_returns_empty_list_for_empty_input(self):
        self.assertEqual(self.tool._collect_image_artifacts([]), [])

    # --- tuple input (SearchAndRerankKB returns (docs, sources)) ---

    def test_unwraps_tuple_and_collects_from_first_element(self):
        data = ([_make_doc(with_image=True, mime_type="image/png", encoded_url="url-png"), _make_doc()], ["source1"])
        with patch(_FILE_SERVICE_PATH, return_value="png-b64"):
            result = self.tool._collect_image_artifacts(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["mime_type"], "image/png")

    def test_tuple_with_no_image_docs_returns_empty_list(self):
        self.assertEqual(self.tool._collect_image_artifacts(([_make_doc()], ["s1"])), [])

    def test_artifact_dict_has_exactly_data_and_mime_type_keys(self):
        with patch(_FILE_SERVICE_PATH, return_value="webp-b64"):
            result = self.tool._collect_image_artifacts([_make_doc(with_image=True, mime_type="image/webp")])
        self.assertEqual(set(result[0].keys()), {"data", "mime_type"})


# ---------------------------------------------------------------------------
# execute() — return type (image pipeline)
# ---------------------------------------------------------------------------


class TestExecuteReturnType(unittest.TestCase):
    """execute() always returns SearchKBResponse regardless of whether images were found."""

    @patch("codemie.agents.tools.kb.search_kb.SearchKBTool.process_llm_routing_index")
    def test_llm_routing_returns_search_kb_response(self, mock_process):
        mock_process.return_value = "routed text"
        tool = _make_tool(index_type="llm_routing_google")
        result = tool.execute(query="q")
        assert isinstance(result, SearchKBResponse)
        self.assertEqual(result.text, "routed text")
        self.assertEqual(result.image_artifacts, [])

    @patch("codemie.agents.tools.kb.search_kb.SearchKBTool.process_knowledge_base_bedrock_index")
    def test_bedrock_returns_search_kb_response(self, mock_bedrock):
        mock_bedrock.return_value = "bedrock text"
        tool = _make_tool(index_type=IndexInfoType.KB_BEDROCK.value)
        result = tool.execute(query="q")
        assert isinstance(result, SearchKBResponse)
        self.assertEqual(result.image_artifacts, [])

    @patch("codemie.agents.tools.kb.search_kb.SearchAndRerankKB")
    def test_standard_path_no_images_returns_search_kb_response_with_empty_artifacts(self, mock_search_class):
        mock_instance = MagicMock()
        mock_instance.execute.return_value = [_make_doc()]
        mock_search_class.return_value = mock_instance
        tool = _make_tool(index_type="kb")
        with patch.object(tool, "metadata", {"request_id": None}):
            result = tool.execute(query="q")
        assert isinstance(result, SearchKBResponse)
        self.assertEqual(result.image_artifacts, [])

    @patch("codemie.agents.tools.kb.search_kb.SearchAndRerankKB")
    def test_standard_path_with_images_populates_image_artifacts(self, mock_search_class):
        mock_instance = MagicMock()
        mock_instance.execute.return_value = [_make_doc(with_image=True, encoded_url="url-img")]
        mock_search_class.return_value = mock_instance
        tool = _make_tool(index_type="kb")
        with patch.object(tool, "metadata", {"request_id": None}):
            with patch(_FILE_SERVICE_PATH, return_value="b64data"):
                result = tool.execute(query="q")
        assert isinstance(result, SearchKBResponse)
        self.assertEqual(len(result.image_artifacts), 1)

    @patch("codemie.agents.tools.kb.search_kb.SearchAndRerankKB")
    def test_search_kb_response_str_returns_text(self, mock_search_class):
        mock_instance = MagicMock()
        mock_instance.execute.return_value = [_make_doc()]
        mock_search_class.return_value = mock_instance
        tool = _make_tool(index_type="kb")
        with patch.object(tool, "metadata", {"request_id": None}):
            result = tool.execute(query="q")
        self.assertIsInstance(str(result), str)

    @patch("codemie.agents.tools.kb.search_kb.SearchAndRerankMarketplace")
    @patch("codemie.agents.tools.kb.search_kb.SearchAndRerankKB")
    def test_platform_assistant_uses_marketplace_class(self, mock_kb_class, mock_marketplace_class):
        mock_instance = MagicMock()
        mock_instance.execute.return_value = [_make_doc()]
        mock_marketplace_class.return_value = mock_instance
        tool = _make_tool(index_type=FullDatasourceTypes.PLATFORM_ASSISTANT.value)
        with patch.object(tool, "metadata", {"request_id": None}):
            tool.execute(query="q")
        mock_marketplace_class.assert_called_once()
        mock_kb_class.assert_not_called()


# ---------------------------------------------------------------------------
# _limit_output_content
# ---------------------------------------------------------------------------


class TestLimitOutputContent(unittest.TestCase):
    """_limit_output_content must limit only the text, preserving image_artifacts."""

    def test_search_kb_response_limits_text_only(self):
        tool = _make_tool()
        artifacts = [{"data": "b64", "mime_type": "image/png"}]
        response = SearchKBResponse(text="long text to limit", image_artifacts=artifacts)

        # Patch parent's _limit_output_content to simulate truncation
        with patch.object(tool.__class__.__bases__[0], "_limit_output_content", return_value=("short", 3)):
            result, token_count = tool._limit_output_content(response)

        self.assertIsInstance(result, SearchKBResponse)
        self.assertEqual(result.text, "short")
        # Image artifacts must survive the limit
        self.assertEqual(result.image_artifacts, artifacts)
        self.assertEqual(token_count, 3)

    def test_search_kb_response_image_artifacts_unchanged(self):
        tool = _make_tool()
        original_artifacts = [{"data": "abc", "mime_type": "image/jpeg"}]
        response = SearchKBResponse(text="text", image_artifacts=original_artifacts)

        with patch.object(tool.__class__.__bases__[0], "_limit_output_content", return_value=("text", 1)):
            result, _ = tool._limit_output_content(response)

        self.assertEqual(result.image_artifacts, original_artifacts)


# ---------------------------------------------------------------------------
# _post_process_output_content
# ---------------------------------------------------------------------------


class TestPostProcessOutputContent(unittest.TestCase):
    """_post_process_output_content produces (text, artifact) for the artifact pipeline."""

    def test_search_kb_response_returns_tuple_with_artifacts(self):
        artifacts = [{"data": "b64img", "mime_type": "image/png"}]
        response = SearchKBResponse(text="description", image_artifacts=artifacts)
        tool = _make_tool()
        text, returned_artifacts = tool._post_process_output_content(response)
        self.assertEqual(text, "description")
        self.assertEqual(returned_artifacts, artifacts)

    def test_search_kb_response_text_is_processed_by_parent(self):
        # parent's _post_process_output_content returns the string as-is (since it's already a str)
        response = SearchKBResponse(text="raw text", image_artifacts=[])
        tool = _make_tool()
        text, _ = tool._post_process_output_content(response)
        self.assertEqual(text, "raw text")

    def test_empty_image_artifacts_is_falsy(self):
        # empty list is falsy → _image_artifact_pre_model_hook skips it safely
        response = SearchKBResponse(text="some text", image_artifacts=[])
        tool = _make_tool()
        _, artifact = tool._post_process_output_content(response)
        self.assertFalse(artifact)


# ---------------------------------------------------------------------------
# SearchKBResponse.__str__
# ---------------------------------------------------------------------------


class TestSearchKBResponseStr(unittest.TestCase):
    """SearchKBResponse.__str__ must return only the text portion."""

    def test_str_returns_text_field(self):
        response = SearchKBResponse(text="result text", image_artifacts=[{"data": "b64", "mime_type": "image/png"}])
        self.assertEqual(str(response), "result text")

    def test_str_with_empty_text_returns_empty_string(self):
        response = SearchKBResponse(text="", image_artifacts=[])
        self.assertEqual(str(response), "")

    def test_str_does_not_include_artifact_data(self):
        artifacts = [{"data": "secretb64", "mime_type": "image/png"}]
        response = SearchKBResponse(text="short answer", image_artifacts=artifacts)
        self.assertNotIn("secretb64", str(response))


# ---------------------------------------------------------------------------
# format_document / format_response
# ---------------------------------------------------------------------------


class TestFormatDocument(unittest.TestCase):
    """format_document structures source + content for KB output."""

    def setUp(self):
        self.tool = _make_tool()

    def test_format_document_includes_source_in_output(self):
        doc = Document(page_content="content", metadata={"source": "file.txt"})
        result = self.tool.format_document(doc)
        self.assertIn("file.txt", result)

    def test_format_document_includes_page_content(self):
        doc = Document(page_content="The answer is 42", metadata={"source": "file.txt"})
        result = self.tool.format_document(doc)
        self.assertIn("The answer is 42", result)

    def test_format_document_appends_chunk_num_when_present(self):
        doc = Document(page_content="chunk body", metadata={"source": "file.txt", "chunk_num": 3})
        result = self.tool.format_document(doc)
        self.assertIn("file.txt-3", result)

    def test_format_document_no_chunk_num_omits_suffix(self):
        doc = Document(page_content="body", metadata={"source": "doc.md"})
        result = self.tool.format_document(doc)
        self.assertIn("doc.md", result)
        self.assertNotIn("doc.md-", result)


class TestFormatResponse(unittest.TestCase):
    """format_response handles both plain list and (docs, sources) tuple."""

    def setUp(self):
        self.tool = _make_tool()

    def test_format_response_list_returns_joined_documents(self):
        docs = [
            Document(page_content="first", metadata={"source": "a.txt"}),
            Document(page_content="second", metadata={"source": "b.txt"}),
        ]
        result = self.tool.format_response(docs)
        self.assertIn("first", result)
        self.assertIn("second", result)
        self.assertIn("a.txt", result)
        self.assertIn("b.txt", result)

    def test_format_response_tuple_includes_second_element_prefix(self):
        docs = [Document(page_content="doc content", metadata={"source": "x.txt"})]
        sources = ["Summary line"]
        result = self.tool.format_response((docs, sources))
        self.assertIn("Summary line", result)
        self.assertIn("doc content", result)

    def test_format_response_empty_list_returns_empty_string(self):
        result = self.tool.format_response([])
        self.assertEqual(result, "")


# ---------------------------------------------------------------------------
# Datasource health notices
# ---------------------------------------------------------------------------


class TestBuildHealthNotice:
    def test_returns_empty_when_kb_index_is_none(self):
        tool = SearchKBTool.model_construct(index_info=None)
        assert tool._build_health_notice() == ""

    def test_reindexing_notice_when_not_completed(self):
        tool = _make_tool(error=False, completed=False, repo_name="my-repo")
        notice = tool._build_health_notice()
        assert "is currently being re-indexed" in notice
        assert "my-repo" in notice

    def test_failed_notice_when_error_true(self):
        tool = _make_tool(error=True, completed=False, repo_name="my-repo")
        notice = tool._build_health_notice()
        assert "last indexing attempt failed" in notice
        assert "my-repo" in notice

    def test_failed_takes_priority_over_not_completed(self):
        tool = _make_tool(error=True, completed=False, repo_name="my-repo")
        notice = tool._build_health_notice()
        assert "last indexing attempt failed" in notice
        assert "re-indexed" not in notice

    def test_stale_notice_when_triggered_at_after_update_date(self):
        now = datetime.now()
        tool = _make_tool(
            error=False,
            completed=True,
            last_reindex_triggered_at=now,
            update_date=now - timedelta(hours=1),
            repo_name="my-repo",
        )
        notice = tool._build_health_notice()
        assert "scheduled reindex that did not complete" in notice
        assert "my-repo" in notice

    def test_no_notice_when_healthy(self):
        now = datetime.now()
        tool = _make_tool(
            error=False,
            completed=True,
            last_reindex_triggered_at=now - timedelta(hours=1),
            update_date=now,
        )
        assert tool._build_health_notice() == ""

    def test_no_notice_when_triggered_at_is_none(self):
        tool = _make_tool(error=False, completed=True, last_reindex_triggered_at=None, update_date=datetime.now())
        assert tool._build_health_notice() == ""

    def test_no_notice_when_update_date_is_none(self):
        tool = _make_tool(error=False, completed=True, last_reindex_triggered_at=datetime.now(), update_date=None)
        assert tool._build_health_notice() == ""

    def test_stale_not_triggered_when_not_completed(self):
        now = datetime.now()
        tool = _make_tool(
            error=False,
            completed=False,
            last_reindex_triggered_at=now,
            update_date=now - timedelta(hours=1),
        )
        notice = tool._build_health_notice()
        assert "is currently being re-indexed" in notice
        assert "scheduled reindex" not in notice

    def test_failed_notice_includes_error_message(self):
        tool = _make_tool(error=True, text="Connection refused")
        notice = tool._build_health_notice()
        assert "Connection refused" in notice

    def test_failed_notice_no_error_message_when_text_none(self):
        tool = _make_tool(error=True, text=None)
        notice = tool._build_health_notice()
        assert "last indexing attempt failed" in notice
        assert "Error:" not in notice


class TestBuildDescriptionHealthPrefix:
    def test_healthy_returns_empty(self):
        prefix = SearchKBTool._build_description_health_prefix(_make_tool(error=False, completed=True).index_info)
        assert prefix == ""

    def test_failed_returns_prefix(self):
        prefix = SearchKBTool._build_description_health_prefix(_make_tool(error=True).index_info)
        assert "[DATASOURCE STATUS: FAILED]" in prefix

    def test_reindexing_returns_prefix(self):
        prefix = SearchKBTool._build_description_health_prefix(_make_tool(error=False, completed=False).index_info)
        assert "[DATASOURCE STATUS: REINDEXING]" in prefix

    def test_stale_returns_prefix(self):
        now = datetime.now()
        prefix = SearchKBTool._build_description_health_prefix(
            _make_tool(
                error=False, completed=True, last_reindex_triggered_at=now, update_date=now - timedelta(hours=1)
            ).index_info
        )
        assert "[DATASOURCE STATUS: STALE]" in prefix


class TestWrapResult:
    def test_empty_notice_returns_result_unchanged(self):
        tool = _make_tool()
        assert tool._wrap_result("some result", "") == "some result"

    def test_string_result_has_notice_prepended(self):
        tool = _make_tool()
        result = tool._wrap_result("content", "[DATASOURCE STATUS: FAILED] failed.\n\n")
        assert result.startswith("[DATASOURCE STATUS: FAILED]")
        assert "content" in result

    def test_non_string_result_passed_through_unchanged(self):
        tool = _make_tool()
        non_str = [{"doc": "a"}]
        with patch("codemie.agents.tools.datasource_health_mixin.logger") as mock_log:
            result = tool._wrap_result(non_str, "[DATASOURCE STATUS: FAILED] failed.\n\n")
        assert result is non_str
        mock_log.warning.assert_called_once()


class TestExecutePrependsNotice:
    @patch("codemie.agents.tools.kb.search_kb.SearchAndRerankKB")
    def test_standard_search_prepends_reindexing_notice(self, mock_search_class):
        mock_search_class.return_value.execute.return_value = []
        tool = _make_tool(error=False, completed=False, repo_name="my-repo")
        tool.index_info.index_type = "knowledge_base_jira"
        result = tool.execute(query="what is X")
        assert "is currently being re-indexed" in result.text

    @patch("codemie.agents.tools.kb.search_kb.SearchAndRerankKB")
    def test_standard_search_no_notice_when_healthy(self, mock_search_class):
        mock_search_class.return_value.execute.return_value = []
        tool = _make_tool(error=False, completed=True)
        tool.index_info.index_type = "knowledge_base_jira"
        result = tool.execute(query="what is X")
        assert "re-indexed" not in result.text
        assert "indexing attempt failed" not in result.text


class TestRunRaisesToolException:
    def test_raises_tool_exception_when_failed(self):
        tool = _make_tool(error=True)
        with pytest.raises(ToolException) as exc_info:
            tool._run(query="what is X")
        assert "last indexing attempt failed" in str(exc_info.value)

    @patch("codemie.agents.tools.kb.search_kb.SearchAndRerankKB")
    def test_no_exception_when_reindexing(self, mock_search_class):
        mock_search_class.return_value.execute.return_value = []
        tool = _make_tool(error=False, completed=False)
        tool.index_info.index_type = "knowledge_base_jira"
        result = tool._run(query="what is X")
        assert "is currently being re-indexed" in result[0]

    @patch("codemie.agents.tools.kb.search_kb.SearchAndRerankKB")
    def test_no_exception_when_healthy(self, mock_search_class):
        mock_search_class.return_value.execute.return_value = []
        tool = _make_tool(error=False, completed=True)
        tool.index_info.index_type = "knowledge_base_jira"
        result = tool._run(query="what is X")
        assert "re-indexed" not in result[0]
        assert "indexing attempt failed" not in result[0]

    @patch("codemie.agents.tools.kb.search_kb.SearchAndRerankKB")
    def test_no_exception_when_stale(self, mock_search_class):
        now = datetime.now()
        mock_search_class.return_value.execute.return_value = []
        tool = _make_tool(
            error=False,
            completed=True,
            last_reindex_triggered_at=now,
            update_date=now - timedelta(hours=1),
        )
        tool.index_info.index_type = "knowledge_base_jira"
        result = tool._run(query="what is X")
        assert "scheduled reindex that did not complete" in result[0]


# ---------------------------------------------------------------------------
# Result completeness
# ---------------------------------------------------------------------------


def _chunk(source: str, chunk_num: int, text: str) -> Document:
    return Document(page_content=text, metadata={"source": source, "chunk_num": chunk_num})


def test_partial_coverage_is_declared_and_actionable():
    """A response missing parts of a document must say so and say what to do about it."""
    tool = _make_tool()
    source = "https://host/report.docx"
    tool.source_chunk_totals = {source: 4}

    result = tool.format_response([_chunk(source, 1, "part one"), _chunk(source, 2, "part two")])

    assert "2 of 4" in result
    assert "narrower" in result.lower()
    assert "do not" in result.lower()


def test_complete_coverage_carries_no_omission_claim():
    """A response covering every part must not imply that something is missing."""
    tool = _make_tool()
    source = "https://host/report.docx"
    tool.source_chunk_totals = {source: 2}

    result = tool.format_response([_chunk(source, 1, "part one"), _chunk(source, 2, "part two")])

    assert "2 of 2" in result
    assert "narrower" not in result.lower()


def test_unknown_total_is_stated_not_omitted():
    """Silence would leave a partial result shaped exactly like a complete one — the very
    defect this change exists to remove. An unknown total must be said out loud."""
    tool = _make_tool()
    tool.source_chunk_totals = {}

    result = tool.format_response([_chunk("https://host/report.docx", 1, "part one")])

    head = result.split("**Source:**")[0]
    assert "total not verified" in head.lower()


def test_notice_precedes_the_document_blocks():
    """Every downstream reducer cuts from the tail, so a notice placed last is the first
    thing lost. It has to sit with the coverage lines, ahead of the content."""
    tool = _make_tool()
    source = "https://host/report.docx"
    tool.source_chunk_totals = {source: 4}

    result = tool.format_response([_chunk(source, 1, "part one")])

    assert result.index("narrower") < result.index("**Source:**")


def test_overlimit_source_does_not_promise_a_successful_retry():
    """A source above the full-retrieval limit stays excluded from document routing no
    matter how narrow the query is, so the notice must not promise that retrying with a
    narrower query can obtain the missing parts."""
    tool = _make_tool()
    source = "https://host/big-report.docx"
    tool.source_chunk_totals = {source: 35}
    tool.full_retrieval_chunk_limit = 20

    result = tool.format_response([_chunk(source, 1, "part one"), _chunk(source, 2, "part two")])

    assert "2 of 35" in result
    assert "full-retrieval limit" in result
    assert "To obtain a missing part, call this tool again" not in result
    assert "not guaranteed" in result.lower()
    assert "do not" in result.lower()


def test_under_limit_source_keeps_the_retry_advice():
    """Sources within the limit remain fully retrievable — the actionable retry advice
    must stay exactly as before when the limit is known."""
    tool = _make_tool()
    source = "https://host/report.docx"
    tool.source_chunk_totals = {source: 4}
    tool.full_retrieval_chunk_limit = 20

    result = tool.format_response([_chunk(source, 1, "part one")])

    assert "1 of 4" in result
    assert "full-retrieval limit" not in result
    assert "narrower" in result.lower()


def test_truncation_rewrites_coverage_claims():
    """A '2 of 2 parts included' claim survives at the head of a tail-truncated output
    while the parts themselves are cut — the claim must be rewritten, not left false."""
    tool = _make_tool()
    source = "https://host/report.docx"
    tool.source_chunk_totals = {source: 2}
    tool.tokens_size_limit = 60

    full_text = tool.format_response([_chunk(source, 1, "part one " * 100), _chunk(source, 2, "part two " * 100)])
    assert "2 of 2 parts included" in full_text

    limited, _ = tool._limit_output_content(SearchKBResponse(text=full_text, image_artifacts=[]))

    assert "part two" not in limited.text
    assert "2 of 2 parts included\n" not in limited.text
    assert "truncated" in limited.text.lower()
    assert "may be missing" in limited.text.lower()


def test_no_truncation_leaves_coverage_claims_untouched():
    """Within the token limit the coverage statement must stay exactly as formatted."""
    tool = _make_tool()
    source = "https://host/report.docx"
    tool.source_chunk_totals = {source: 2}

    full_text = tool.format_response([_chunk(source, 1, "part one"), _chunk(source, 2, "part two")])

    limited, _ = tool._limit_output_content(SearchKBResponse(text=full_text, image_artifacts=[]))

    assert limited.text == full_text


def test_unverified_coverage_carries_the_guardrail():
    """Unknown totals are exactly where completeness cannot be vouched for, so that path
    must not be the one without a caution — otherwise a bare count reads as reassurance."""
    tool = _make_tool()
    tool.source_chunk_totals = {}

    result = tool.format_response([_chunk("https://host/report.docx", 1, "part one")])

    head = result.split("**Source:**")[0]
    assert "do not" in head.lower()
    assert "narrower" in head.lower()
