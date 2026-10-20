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

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from codemie.agents.tools.code.tools import SearchCodeRepoTool, SearchCodeRepoByPathsTool
from codemie.agents.tools.code.read_files_tools import ReadFileFromStorageTool, ReadFileFromStorageWithSummaryTool
from langchain_core.tools import ToolException


def _make_code_fields():
    cf = MagicMock()
    cf.repo_name = "my-repo"
    cf.app_name = "my-project"
    cf.index_type = "code"
    return cf


def _make_tool(
    tool_class=SearchCodeRepoTool,
    error=False,
    completed=True,
    last_reindex_triggered_at=None,
    update_date=None,
    repo_name="my-repo",
    text=None,
):
    index_info = MagicMock()
    index_info.repo_name = repo_name
    index_info.error = error
    index_info.completed = completed
    index_info.last_reindex_triggered_at = last_reindex_triggered_at
    index_info.update_date = update_date
    index_info.text = text

    code_fields = _make_code_fields()

    tool = tool_class.model_construct(
        index_info=index_info,
        code_fields=code_fields,
        metadata={},
        tokens_size_limit=20000,
        top_k=10,
        with_filtering=False,
        is_react=True,
    )
    return tool


class TestBuildHealthNotice:
    def test_returns_empty_when_index_info_is_none(self):
        tool = SearchCodeRepoTool.model_construct(index_info=None)
        assert tool._build_health_notice() == ""

    def test_reindexing_notice_when_not_completed(self):
        tool = _make_tool(error=False, completed=False)
        notice = tool._build_health_notice()
        assert "is currently being re-indexed" in notice
        assert "my-repo" in notice

    def test_failed_notice_when_error_true(self):
        tool = _make_tool(error=True, completed=False)
        notice = tool._build_health_notice()
        assert "last indexing attempt failed" in notice
        assert "my-repo" in notice

    def test_failed_takes_priority_over_not_completed(self):
        tool = _make_tool(error=True, completed=False)
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


class TestManualReindexStampHealth:
    """Health states for a datasource whose last_reindex_triggered_at is stamped at trigger time."""

    def test_stamped_and_in_progress_reads_reindexing(self):
        now = datetime.now()
        tool = _make_tool(
            error=False, completed=False, last_reindex_triggered_at=now, update_date=now - timedelta(hours=1)
        )
        assert "is currently being re-indexed" in tool._build_health_notice()

    def test_stamped_and_failed_reads_failed(self):
        now = datetime.now()
        tool = _make_tool(
            error=True, completed=False, last_reindex_triggered_at=now, update_date=now - timedelta(hours=1)
        )
        assert "last indexing attempt failed" in tool._build_health_notice()

    def test_stamped_and_completed_without_update_reads_stale(self):
        now = datetime.now()
        tool = _make_tool(
            error=False, completed=True, last_reindex_triggered_at=now, update_date=now - timedelta(hours=1)
        )
        assert "scheduled reindex that did not complete" in tool._build_health_notice()

    def test_update_after_stamp_on_success_reads_ok(self):
        now = datetime.now()
        tool = _make_tool(
            error=False, completed=True, last_reindex_triggered_at=now - timedelta(minutes=5), update_date=now
        )
        assert tool._build_health_notice() == ""


class TestBuildDescriptionHealthPrefix:
    def test_healthy_returns_empty(self):
        prefix = SearchCodeRepoTool._build_description_health_prefix(_make_tool(error=False, completed=True).index_info)
        assert prefix == ""

    def test_failed_returns_prefix(self):
        prefix = SearchCodeRepoTool._build_description_health_prefix(_make_tool(error=True).index_info)
        assert "[DATASOURCE STATUS: FAILED]" in prefix

    def test_reindexing_returns_prefix(self):
        prefix = SearchCodeRepoTool._build_description_health_prefix(
            _make_tool(error=False, completed=False).index_info
        )
        assert "[DATASOURCE STATUS: REINDEXING]" in prefix

    def test_stale_returns_prefix(self):
        now = datetime.now()
        prefix = SearchCodeRepoTool._build_description_health_prefix(
            _make_tool(
                error=False, completed=True, last_reindex_triggered_at=now, update_date=now - timedelta(hours=1)
            ).index_info
        )
        assert "[DATASOURCE STATUS: STALE]" in prefix


class TestRunRaisesToolException:
    def test_raises_tool_exception_when_failed(self):
        tool = _make_tool(error=True)
        with pytest.raises(ToolException) as exc_info:
            tool._run(query="what is X")
        assert "last indexing attempt failed" in str(exc_info.value)

    @patch("codemie.agents.tools.code.tools.SearchAndRerankCode")
    def test_no_exception_when_reindexing(self, mock_search_class):
        mock_search_class.return_value.execute.return_value = []
        tool = _make_tool(error=False, completed=False)
        result = tool._run(query="what is X")
        assert "is currently being re-indexed" in result

    @patch("codemie.agents.tools.code.tools.SearchAndRerankCode")
    def test_no_exception_when_healthy(self, mock_search_class):
        mock_search_class.return_value.execute.return_value = []
        now = datetime.now()
        tool = _make_tool(
            error=False,
            completed=True,
            last_reindex_triggered_at=now - timedelta(hours=1),
            update_date=now,
        )
        result = tool._run(query="what is X")
        assert "re-indexed" not in result
        assert "indexing attempt failed" not in result

    @patch("codemie.agents.tools.code.tools.SearchAndRerankCode")
    def test_no_exception_when_stale(self, mock_search_class):
        now = datetime.now()
        mock_search_class.return_value.execute.return_value = []
        tool = _make_tool(
            error=False,
            completed=True,
            last_reindex_triggered_at=now,
            update_date=now - timedelta(hours=1),
        )
        result = tool._run(query="what is X")
        assert "scheduled reindex that did not complete" in result

    def test_raises_tool_exception_when_failed_by_paths_tool(self):
        tool = _make_tool(tool_class=SearchCodeRepoByPathsTool, error=True)
        with pytest.raises(ToolException) as exc_info:
            tool._run(query="what is X", file_path=[])
        assert "last indexing attempt failed" in str(exc_info.value)

    @patch("codemie.agents.tools.code.tools.SearchAndRerankCode")
    def test_no_exception_when_reindexing_by_paths_tool(self, mock_search_class):
        mock_search_class.return_value.execute.return_value = []
        tool = _make_tool(tool_class=SearchCodeRepoByPathsTool, error=False, completed=False)
        result = tool._run(query="what is X", file_path=[])
        assert "is currently being re-indexed" in result


class TestRunPrependsNotice:
    @patch("codemie.agents.tools.code.tools.SearchAndRerankCode")
    def test_reindexing_notice_in_result(self, mock_search_class):
        mock_search_class.return_value.execute.return_value = []
        tool = _make_tool(error=False, completed=False)
        result = tool._run(query="what is X")
        assert "is currently being re-indexed" in result

    @patch("codemie.agents.tools.code.tools.SearchAndRerankCode")
    def test_stale_notice_in_result(self, mock_search_class):
        now = datetime.now()
        mock_search_class.return_value.execute.return_value = []
        tool = _make_tool(
            error=False,
            completed=True,
            last_reindex_triggered_at=now,
            update_date=now - timedelta(hours=1),
        )
        result = tool._run(query="what is X")
        assert "scheduled reindex that did not complete" in result


def _make_read_tool(
    tool_class=ReadFileFromStorageTool,
    error=False,
    completed=True,
    last_reindex_triggered_at=None,
    update_date=None,
    repo_name="my-repo",
    text=None,
):
    index_info = MagicMock()
    index_info.repo_name = repo_name
    index_info.error = error
    index_info.completed = completed
    index_info.last_reindex_triggered_at = last_reindex_triggered_at
    index_info.update_date = update_date
    index_info.text = text

    code_fields = _make_code_fields()

    return tool_class.model_construct(
        index_info=index_info,
        code_fields=code_fields,
        metadata={},
        tokens_size_limit=20000,
    )


_GET_REPO_FILES_PATH = "codemie.agents.tools.code.read_files_tools.get_repo_files_by_search_phrase_path"

# Each entry: (tool_class, run_kwargs)
_READ_FILE_TOOL_CASES = [
    (ReadFileFromStorageTool, {"file_path": "src/main.py"}),
    (ReadFileFromStorageWithSummaryTool, {"file_path": "src/main.py", "summarization_instructions": "summarize"}),
]


class TestReadFileToolsHealth:
    @pytest.mark.parametrize("tool_class,run_kwargs", _READ_FILE_TOOL_CASES)
    def test_failed_raises_tool_exception(self, tool_class, run_kwargs):
        tool = _make_read_tool(tool_class=tool_class, error=True)
        with pytest.raises(ToolException) as exc_info:
            tool._run(**run_kwargs)
        assert "last indexing attempt failed" in str(exc_info.value)

    @pytest.mark.parametrize("tool_class,run_kwargs", _READ_FILE_TOOL_CASES)
    @patch(_GET_REPO_FILES_PATH, return_value=[])
    def test_reindexing_notice_in_result(self, _mock, tool_class, run_kwargs):
        tool = _make_read_tool(tool_class=tool_class, error=False, completed=False)
        result = tool._run(**run_kwargs)
        assert "is currently being re-indexed" in result

    @pytest.mark.parametrize("tool_class,run_kwargs", _READ_FILE_TOOL_CASES)
    @patch(_GET_REPO_FILES_PATH, return_value=[])
    def test_stale_notice_in_result(self, _mock, tool_class, run_kwargs):
        now = datetime.now()
        tool = _make_read_tool(
            tool_class=tool_class,
            error=False,
            completed=True,
            last_reindex_triggered_at=now,
            update_date=now - timedelta(hours=1),
        )
        result = tool._run(**run_kwargs)
        assert "scheduled reindex that did not complete" in result

    @pytest.mark.parametrize("tool_class,run_kwargs", _READ_FILE_TOOL_CASES)
    @patch(_GET_REPO_FILES_PATH, return_value=[])
    def test_healthy_no_notice(self, _mock, tool_class, run_kwargs):
        now = datetime.now()
        tool = _make_read_tool(
            tool_class=tool_class,
            error=False,
            completed=True,
            last_reindex_triggered_at=now - timedelta(hours=1),
            update_date=now,
        )
        result = tool._run(**run_kwargs)
        assert "re-indexed" not in result
        assert "indexing attempt failed" not in result
        assert "scheduled reindex" not in result
