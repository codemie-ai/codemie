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

from codemie.datasource.file.file_datasource_update_processor import FileDatasourceUpdateProcessor
from codemie.datasource.file.file_datasource_processor import FILE_PATH_DATA_NT
from codemie.rest_api.models.index import GuardrailBlockedException, IndexDeletedException, IndexInfo
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user1"
    user.username = "user1@example.com"
    user.name = "User One"
    return user


@pytest.fixture
def mock_index():
    index = MagicMock(spec=IndexInfo)
    index.uploaded_files = ["old_file.txt", "kept_file.txt"]
    index.processed_files = ["old_file.txt", "kept_file.txt"]
    index.processing_info = {"total_documents": 5, "documents_count_key": 2}
    index.current_state = 2
    index.complete_state = 2
    index.description = "old description"
    index.project_space_visible = False
    index.updated_by = None
    return index


@pytest.fixture
def make_processor(mock_user, mock_index):
    """Factory that builds a processor with sensible defaults, overridable per test."""

    def _make(
        files_paths=None,
        uploaded_files=None,
        new_files_paths=None,
        removed_files=None,
        **extra,
    ):
        files_paths = files_paths or [
            FILE_PATH_DATA_NT(name="kept_file.txt", owner="user1"),
            FILE_PATH_DATA_NT(name="new_file.txt", owner="user1"),
        ]
        uploaded_files = uploaded_files or ["kept_file.txt", "new_file.txt"]
        new_files_paths = (
            new_files_paths
            if new_files_paths is not None
            else [
                FILE_PATH_DATA_NT(name="new_file.txt", owner="user1"),
            ]
        )
        removed_files = removed_files if removed_files is not None else {"old_file.txt"}

        with patch.object(FileDatasourceUpdateProcessor, '_assign_and_sync_guardrails'):
            proc = FileDatasourceUpdateProcessor(
                datasource_name="test_ds",
                user=mock_user,
                files_paths=files_paths,
                project_name="test_project",
                index=mock_index,
                uploaded_files=uploaded_files,
                new_files_paths=new_files_paths,
                removed_files=removed_files,
                **extra,
            )
        return proc

    return _make


class TestInit:
    def test_new_files_paths_stored_as_attribute(self, make_processor):
        new_fp = [FILE_PATH_DATA_NT(name="new_file.txt", owner="user1")]
        proc = make_processor(new_files_paths=new_fp)
        assert proc.new_files_paths == new_fp

    def test_removed_files_stored_as_attribute(self, make_processor):
        removed = {"old_file.txt"}
        proc = make_processor(removed_files=removed)
        assert proc.removed_files == removed

    def test_new_files_paths_not_forwarded_to_parent(self, mock_user, mock_index):
        """new_files_paths must NOT appear in FileDatasourceProcessor / BaseDatasourceProcessor."""
        new_fp = [FILE_PATH_DATA_NT(name="new_file.txt", owner="user1")]
        with patch.object(FileDatasourceUpdateProcessor, '_assign_and_sync_guardrails'):
            proc = FileDatasourceUpdateProcessor(
                datasource_name="test_ds",
                user=mock_user,
                files_paths=[
                    FILE_PATH_DATA_NT(name="kept_file.txt", owner="user1"),
                    FILE_PATH_DATA_NT(name="new_file.txt", owner="user1"),
                ],
                project_name="test_project",
                index=mock_index,
                uploaded_files=["kept_file.txt", "new_file.txt"],
                new_files_paths=new_fp,
                removed_files={"old_file.txt"},
            )
        # Parent stores the full combined files_paths list, NOT the new_files_paths subset
        all_fps = [
            FILE_PATH_DATA_NT(name="kept_file.txt", owner="user1"),
            FILE_PATH_DATA_NT(name="new_file.txt", owner="user1"),
        ]
        assert proc.files_paths == all_fps
        assert proc.new_files_paths == new_fp

    def test_empty_removed_files_accepted(self, make_processor):
        proc = make_processor(removed_files=set())
        assert proc.removed_files == set()

    def test_empty_new_files_paths_accepted(self, make_processor):
        proc = make_processor(new_files_paths=[])
        assert proc.new_files_paths == []


class TestInitIndex:
    def test_removed_files_stripped_from_processed_files(self, make_processor, mock_index):
        mock_index.processed_files = ["old_file.txt", "kept_file.txt"]
        proc = make_processor(removed_files={"old_file.txt"})
        with patch.object(proc, '_assign_and_sync_guardrails'):
            proc.init_index()
        assert "old_file.txt" not in mock_index.processed_files
        assert "kept_file.txt" in mock_index.processed_files

    def test_uploaded_files_set_to_actual_list(self, make_processor, mock_index):
        expected = ["kept_file.txt", "new_file.txt"]
        proc = make_processor(uploaded_files=expected)
        with patch.object(proc, '_assign_and_sync_guardrails'):
            proc.init_index()
        assert list(mock_index.uploaded_files) == expected

    def test_no_removed_files_leaves_processed_files_intact(self, make_processor, mock_index):
        mock_index.processed_files = ["kept_file.txt"]
        proc = make_processor(removed_files=set())
        with patch.object(proc, '_assign_and_sync_guardrails'):
            proc.init_index()
        assert mock_index.processed_files == ["kept_file.txt"]

    def test_current_state_decremented_by_removed_count(self, make_processor, mock_index):
        mock_index.current_state = 5
        proc = make_processor(removed_files={"old_file.txt"})  # 1 removed
        with patch.object(proc, '_assign_and_sync_guardrails'):
            proc.init_index()
        assert mock_index.current_state == 4

    def test_complete_state_adjusted_by_removed_and_new(self, make_processor, mock_index):
        mock_index.complete_state = 5
        # 1 removed, 1 new_file
        proc = make_processor(
            removed_files={"old_file.txt"},
            new_files_paths=[FILE_PATH_DATA_NT(name="new_file.txt", owner="user1")],
        )
        with patch.object(proc, '_assign_and_sync_guardrails'):
            proc.init_index()
        assert mock_index.complete_state == 5  # 5 - 1 + 1 = 5

    def test_processing_info_uses_total_documents_key_as_base(self, make_processor, mock_index):
        """_init_index reads total_documents as the base when adjusting both count keys.

        Fixture: total_documents=5, documents_count_key=2. With 1 removed and 1 new:
          result: 5 - 1 + 1 = 5  (reads total_documents as base)
        Both documents_count_key and total_documents are updated to the new value.
        """
        mock_index.processing_info = {"total_documents": 5, "documents_count_key": 2}
        proc = make_processor(
            removed_files={"old_file.txt"},
            new_files_paths=[FILE_PATH_DATA_NT(name="new_file.txt", owner="user1")],
        )
        with patch.object(proc, '_assign_and_sync_guardrails'):
            proc.init_index()
        assert mock_index.processing_info["documents_count_key"] == 5  # 5 - 1 + 1
        assert mock_index.processing_info["total_documents"] == 5  # 5 - 1 + 1

    def test_guardrails_synced(self, make_processor):
        proc = make_processor()
        with patch.object(proc, '_assign_and_sync_guardrails') as mock_sync:
            proc.init_index()
        mock_sync.assert_called_once()

    def test_index_update_called(self, make_processor, mock_index):
        proc = make_processor()
        with patch.object(proc, '_assign_and_sync_guardrails'):
            proc.init_index()
        mock_index.update.assert_called()

    def test_processed_files_none_handled_gracefully(self, make_processor, mock_index):
        """processed_files=None must not raise — the or [] guard must handle it."""
        mock_index.processed_files = None
        proc = make_processor(removed_files={"old_file.txt"})
        with patch.object(proc, '_assign_and_sync_guardrails'):
            proc.init_index()
        assert mock_index.processed_files == []


class TestBackgroundPathsNoInitIndex:
    """Verify _init_index is NOT called inside process() or reprocess() — it runs synchronously
    in the router before scheduling, so calling it again would double-adjust counters."""

    def test_process_does_not_call_init_index(self, make_processor, mock_index):
        proc = make_processor()
        mock_index.project_name = "test_project"
        proc.init_index = MagicMock()

        # Patch everything that process() touches after _init_index
        with (
            patch.object(proc, '_assign_and_sync_guardrails'),
            patch('codemie.datasource.file.file_datasource_update_processor.ensure_application_exists'),
            patch('codemie.service.llm_service.utils.set_llm_context'),
            patch('codemie.datasource.file.file_datasource_update_processor.set_logging_info'),
            patch('codemie.datasource.file.file_datasource_update_processor.propagated_span') as mock_span,
            patch.object(proc, '_init_loader', return_value=MagicMock()),
            patch.object(proc, '_on_process_start'),
            patch.object(proc, '_process', return_value=MagicMock()),
            patch.object(proc, '_persist_load_stats', return_value={}),
            patch.object(proc, '_on_process_end'),
            patch.object(proc, '_validate_indexing_result'),
            patch.object(proc, '_create_or_update_scheduler'),
        ):
            mock_span.return_value.__enter__ = MagicMock(return_value=None)
            mock_span.return_value.__exit__ = MagicMock(return_value=False)
            mock_index.complete_state = 1
            mock_index.current_state = 1
            proc.loader = MagicMock()
            proc.loader.fetch_remote_stats.return_value = {"documents_count_key": 1}
            proc.process()

        proc.init_index.assert_not_called()
        # Counters must be untouched — double-adjustment would change them
        assert mock_index.current_state == 1
        assert mock_index.complete_state == 1

    def test_reprocess_removals_only_does_not_call_init_index(self, make_processor):
        proc = make_processor(new_files_paths=[])  # no new files → removals-only path
        proc.init_index = MagicMock()

        with (
            patch.object(proc, '_remove_deleted_files_from_es'),
            patch.object(proc, '_on_process_end'),
        ):
            proc.index.complete_state = 1
            proc.index.complete_progress = MagicMock()
            proc.reprocess()

        proc.init_index.assert_not_called()


# ---------------------------------------------------------------------------
# _init_loader
# ---------------------------------------------------------------------------


class TestInitLoader:
    def test_uses_new_files_paths_not_all_files_paths(self, make_processor):
        new_fp = [FILE_PATH_DATA_NT(name="new.txt", owner="user1")]
        proc = make_processor(new_files_paths=new_fp)

        with patch("codemie.datasource.file.file_datasource_update_processor.FilesDatasourceLoader") as mock_loader_cls:
            proc._init_loader()

        call_kwargs = mock_loader_cls.call_args.kwargs
        assert call_kwargs["files_paths"] == new_fp
        assert call_kwargs["total_count_of_documents"] == len(new_fp)


# ---------------------------------------------------------------------------
# reprocess — new-files branch calls process()
# ---------------------------------------------------------------------------


class TestReprocessWithNewFiles:
    def test_reprocess_calls_process_when_new_files_present(self, make_processor):
        proc = make_processor(
            new_files_paths=[FILE_PATH_DATA_NT(name="new.txt", owner="user1")],
            removed_files=set(),
        )
        with (
            patch.object(proc, "_remove_deleted_files_from_es"),
            patch.object(proc, "process") as mock_process,
        ):
            proc.reprocess()

        mock_process.assert_called_once()


# ---------------------------------------------------------------------------
# _remove_deleted_files_from_es
# ---------------------------------------------------------------------------


class TestRemoveDeletedFilesFromEs:
    def test_skips_es_when_removed_files_is_empty(self, make_processor):
        proc = make_processor(removed_files=set())
        with patch.object(proc, "client") as mock_client:
            proc._remove_deleted_files_from_es()
        mock_client.indices.exists.assert_not_called()

    def test_skips_delete_when_es_index_does_not_exist(self, make_processor):
        proc = make_processor(removed_files={"old.txt"})
        with patch.object(proc, "client") as mock_client:
            mock_client.indices.exists.return_value = False
            proc._remove_deleted_files_from_es()
        mock_client.delete_by_query.assert_not_called()

    def test_calls_delete_by_query_for_removed_files(self, make_processor):
        proc = make_processor(removed_files={"old.txt"})
        with patch.object(proc, "client") as mock_client:
            mock_client.indices.exists.return_value = True
            proc._remove_deleted_files_from_es()
        mock_client.delete_by_query.assert_called_once()
        body = mock_client.delete_by_query.call_args.kwargs["body"]
        assert "old.txt" in str(body)

    def test_does_not_raise_on_es_exception(self, make_processor):
        proc = make_processor(removed_files={"old.txt"})
        with patch.object(proc, "client") as mock_client:
            mock_client.indices.exists.side_effect = Exception("ES error")
            proc._remove_deleted_files_from_es()  # must not raise


# ---------------------------------------------------------------------------
# _load_es_chunks_count
# ---------------------------------------------------------------------------


class TestLoadEsChunksCount:
    def test_returns_count_when_index_exists(self, make_processor):
        proc = make_processor()
        with patch.object(proc, "client") as mock_client:
            mock_client.indices.exists.return_value = True
            mock_client.count.return_value = {"count": 42}
            assert proc._load_es_chunks_count() == 42

    def test_returns_zero_when_index_does_not_exist(self, make_processor):
        proc = make_processor()
        with patch.object(proc, "client") as mock_client:
            mock_client.indices.exists.return_value = False
            assert proc._load_es_chunks_count() == 0

    def test_returns_zero_on_exception(self, make_processor):
        proc = make_processor()
        with patch.object(proc, "client") as mock_client:
            mock_client.indices.exists.side_effect = Exception("ES error")
            assert proc._load_es_chunks_count() == 0


# ---------------------------------------------------------------------------
# _on_process_end
# ---------------------------------------------------------------------------


class TestOnProcessEnd:
    def test_syncs_uploaded_and_processed_files_from_self(self, make_processor, mock_index):
        proc = make_processor(uploaded_files=["file1.txt", "file2.txt"])
        with patch.object(proc, "_load_es_chunks_count", return_value=0):
            proc._on_process_end()
        assert list(mock_index.uploaded_files) == list(proc.uploaded_files)
        assert list(mock_index.processed_files) == list(proc.uploaded_files)

    def test_sets_current_chunks_state_from_es_count(self, make_processor, mock_index):
        proc = make_processor()
        with patch.object(proc, "_load_es_chunks_count", return_value=99):
            proc._on_process_end()
        assert mock_index.current__chunks_state == 99

    def test_calls_index_update(self, make_processor, mock_index):
        proc = make_processor()
        with patch.object(proc, "_load_es_chunks_count", return_value=0):
            proc._on_process_end()
        mock_index.update_progress.assert_called()

    def test_description_preserved_through_reindex(self, make_processor, mock_index):
        """Verify update_progress() is called to preserve metadata (EPMCDME-10036)."""
        updated_description = "Updated description"
        proc = make_processor(description=updated_description)
        mock_index.description = "old description"

        with patch.object(proc, "_load_es_chunks_count", return_value=0):
            proc._on_process_end()

        # update_progress should be called, which preserves metadata by using targeted SQL
        mock_index.update_progress.assert_called()

    def test_project_space_visible_preserved_through_reindex(self, make_processor, mock_index):
        """Verify update_progress() is called to preserve metadata (EPMCDME-10036)."""
        updated_visibility = True
        proc = make_processor(project_space_visible=updated_visibility)
        mock_index.project_space_visible = False

        with patch.object(proc, "_load_es_chunks_count", return_value=0):
            proc._on_process_end()

        # update_progress should be called, which preserves metadata by using targeted SQL
        mock_index.update_progress.assert_called()


# ---------------------------------------------------------------------------
# process() exception handlers
# ---------------------------------------------------------------------------


def _run_process_with_exception(proc, mock_index, exception):
    """Context manager: run process() with _process() rigged to raise *exception*."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        mock_index.project_name = "test_project"
        mock_index.is_fetching = False
        mock_index.processing_info = {}
        mock_index.complete_state = 1
        mock_index.current_state = 1

        with (
            patch("codemie.datasource.file.file_datasource_update_processor.ensure_application_exists"),
            patch("codemie.service.llm_service.utils.set_llm_context"),
            patch("codemie.datasource.file.file_datasource_update_processor.set_logging_info"),
            patch("codemie.datasource.file.file_datasource_update_processor.propagated_span") as mock_span,
            patch("codemie.datasource.file.file_datasource_update_processor.DatasourceMonitoringCallback"),
            patch("codemie.datasource.file.file_datasource_update_processor.record_exception_on_span"),
            patch.object(proc, "_init_loader", return_value=MagicMock()),
            patch.object(proc, "_on_process_start"),
            patch.object(proc, "_process", side_effect=exception),
            patch.object(proc, "_persist_load_stats", return_value={}),
            patch.object(proc, "_on_process_end") as mock_on_end,
            patch.object(proc, "client") as mock_client,
        ):
            mock_span.return_value.__enter__ = MagicMock(return_value=None)
            mock_span.return_value.__exit__ = MagicMock(return_value=False)
            proc.loader = MagicMock()
            proc.loader.fetch_remote_stats.return_value = {}
            yield mock_client, mock_on_end

    return _ctx()


class TestProcessExceptionHandlers:
    def test_index_deleted_exception_deletes_es_index_and_ends(self, make_processor, mock_index):
        proc = make_processor()
        with _run_process_with_exception(proc, mock_index, IndexDeletedException("deleted")) as (
            mock_client,
            mock_on_end,
        ):
            proc.process()

        mock_client.indices.delete.assert_called_once()
        mock_on_end.assert_called_once()

    def test_guardrail_blocked_exception_sets_error_and_ends(self, make_processor, mock_index):
        proc = make_processor()
        with _run_process_with_exception(proc, mock_index, GuardrailBlockedException("blocked")) as (
            mock_client,
            mock_on_end,
        ):
            proc.process()

        mock_index.set_error.assert_called_once()
        mock_on_end.assert_called_once()

    def test_generic_exception_reraises_and_sets_error(self, make_processor, mock_index):
        proc = make_processor()
        with _run_process_with_exception(proc, mock_index, ValueError("unexpected")) as (_, mock_on_end):
            with pytest.raises(ValueError):
                proc.process()

        mock_index.set_error.assert_called_once()
        mock_on_end.assert_called_once()
