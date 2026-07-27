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

"""FileDatasourceUpdateProcessor — handles PUT /index/knowledge_base/file logic."""

import time


from codemie.configs import logger
from codemie.configs.logger import set_logging_info
from codemie.core.models import CreatedByUser
from codemie.core.otel_tracing import propagated_span, record_exception_on_span
from codemie.datasource.callback.datasource_monitoring_callback import DatasourceMonitoringCallback
from codemie.datasource.file.file_datasource_processor import FILE_PATH_DATA_NT, FileDatasourceProcessor
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.file_loader import FilesDatasourceLoader
from codemie.rest_api.models.index import GuardrailBlockedException, IndexDeletedException
from codemie.rest_api.utils.default_applications import ensure_application_exists


class FileDatasourceUpdateProcessor(FileDatasourceProcessor):
    """Processor for updating an existing file knowledge-base datasource.

    Extends FileDatasourceProcessor to:
    - Update metadata (description, visibility, updated_by, guardrails) on the
      existing IndexInfo record instead of creating a new one.
    - Persist the final uploaded_files list once reindexing completes.
    - Selectively delete only removed files from ES instead of wiping the whole index.
    - Track and accumulate tokens usage across update runs.

    Usage:
        1. Call ``validate_can_update(index)`` before scheduling.
        2. Instantiate with the existing ``index`` and the combined files list
           (kept + new), then call ``schedule(background_tasks, func=self.reprocess)``.
    """

    def __init__(
        self,
        *args,
        new_files_paths: list[FILE_PATH_DATA_NT],
        removed_files: set[str],
        **kwargs,
    ) -> None:
        # Consumed here — NOT forwarded to FileDatasourceProcessor / BaseDatasourceProcessor.
        self.new_files_paths = new_files_paths
        self.removed_files = removed_files
        super().__init__(*args, **kwargs)
        self.updated_by = CreatedByUser(id=self.user.id, username=self.user.username, name=self.user.name)

    def init_index(self) -> None:
        """Update metadata on the existing IndexInfo record.

        Unlike the parent class which creates a new IndexInfo, this override
        updates the existing one in-place and syncs guardrail assignments.
        Adjusts progress counters to account for removed and new files,
        and updates the processing_info document count accordingly.
        Also cleans up processed_files for removed files and sets uploaded_files
        to the actual new state before processing begins.

        Status flags are conditionally set here (not in process()) so the DB is
        consistent before the background task starts and the API response is returned.
        Flags are only flipped when there are new files to index — for a removal-only
        update the datasource was already completed and no fetch phase will occur:
          - ``completed = False``   — indexing not yet done
          - ``error = False``       — no error yet
          - ``is_fetching = True``  — fetch phase is starting
          - ``is_queued = False``   — task is now active, no longer queued
        """
        removed = self.removed_files

        # Sync file lists — must happen before the background task touches state.
        if removed:
            self.index.processed_files = [f for f in (self.index.processed_files or []) if f not in removed]
        self.index.uploaded_files = list(self.uploaded_files)

        if self.description is not None:
            self.index.description = self.description
        if self.project_space_visible is not None:
            self.index.project_space_visible = self.project_space_visible
        self.index.updated_by = self.updated_by

        removed_count = len(removed)
        new_count = len(self.new_files_paths)
        # Subtract removed files from current progress.
        self.index.current_state = self.index.current_state - removed_count
        # Adjust expected completion counter.
        self.index.complete_state = self.index.complete_state - removed_count + new_count

        # Update processing_info document count.
        existing_info = self.index.processing_info or {}
        total_documents = existing_info.get(BaseDatasourceLoader.TOTAL_DOCUMENTS_KEY, 0) - removed_count + new_count
        self.index.processing_info = {
            **existing_info,
            BaseDatasourceLoader.DOCUMENTS_COUNT_KEY: total_documents,
            BaseDatasourceLoader.TOTAL_DOCUMENTS_KEY: total_documents,
        }

        if self.new_files_paths:
            self.index.completed = False
            self.index.error = False
            self.index.is_fetching = True
            self.index.is_queued = False

        self.index.update()
        self._assign_and_sync_guardrails()

    def _init_loader(self) -> FilesDatasourceLoader:
        """Override loader initialisation to process only newly added files (Item 5).

        Using ``self.new_files_paths`` ensures that ``fetch_remote_stats()`` returns
        ``DOCUMENTS_COUNT_KEY = len(new_files)``, so ``complete_state`` tracks only
        the files that actually need to be (re-)indexed in this run.
        """
        return FilesDatasourceLoader(
            total_count_of_documents=len(self.new_files_paths),
            files_paths=self.new_files_paths,
            csv_separator=self.csv_separator,
            request_uuid=self.request_uuid,
            include_email_attachments=self.include_email_attachments,
        )

    def reprocess(self) -> None:
        """Override reprocess to perform selective ES deletion instead of a full index wipe (Item 1).

        Steps:
        1. Delete only the ES documents belonging to removed files.
        2. If there are new files to index, call process() (which includes init_index).
           Otherwise, handle the metadata-only / removals-only path directly.
        """
        self.is_full_reindex = True

        # Item 1: selectively remove deleted files from ES.
        self._remove_deleted_files_from_es()

        if not self.new_files_paths:
            # Only removals, no new files to index — update metadata and mark completed.
            logger.info(
                f"FileDatasourceUpdateProcessor. NoNewFiles. "
                f"RemovedFiles={len(self.removed_files)}. "
                f"Datasource={self.datasource_name}. "
                "Skipping indexing run — updating metadata only."
            )
            self._on_process_end()
            self.index.complete_progress(self.index.complete_state)
            return

        self.process()

    def process(self) -> None:
        """Override process() to avoid the counter-reset cycle in the base implementation.

        BaseDatasourceProcessor.process() calls start_fetching() then start_progress(), both
        of which invoke _reset_state(is_incremental=False) and zero current_state, complete_state,
        processed_files and current__chunks_state.

        For the update flow those counters must start from the preserved state already set by
        init_index() (current_state = preserved docs, complete_state = preserved + new), not
        from zero.  This override replicates all essential machinery from the base — OTel tracing,
        monitoring callbacks, error handling, load-stats persistence, scheduler — while replacing
        the two reset calls with update-aware status-flag writes that leave the counters intact.
        """
        # Import here to avoid circular import (same pattern as base class).
        from codemie.service.llm_service.utils import set_llm_context

        with propagated_span(
            self._otel_context,
            "datasource.process",
            {"codemie.datasource_name": self.datasource_name},
        ):
            try:
                self._load_stats_persisted = False

                if self.index.project_name:
                    ensure_application_exists(self.index.project_name)

                self.callbacks.append(
                    DatasourceMonitoringCallback(
                        self.index,
                        self.user,
                        (self.is_full_reindex or self.is_incremental_reindex),
                        self.is_resume_indexing,
                        self.request_uuid,
                    )
                )
                if self.user:
                    set_llm_context(None, self.index.project_name, self.user)
                    set_logging_info(uuid=self.request_uuid, user_id=self.user.id, user_email=self.user.username)

                # init_index() already set all counters and status flags
                # (completed, error, is_fetching, is_queued) synchronously before
                # this background task was scheduled — no reset needed here.
                self.loader = self._init_loader()
                self._on_process_start()
                start_time = time.time()

                datasource_remote_stats = self.loader.fetch_remote_stats()
                # Clear fetching flag and merge new-file stats into processing_info.
                # current_state / complete_state are intentionally left unchanged.
                # Exclude document-count keys: init_index() already computed the correct
                # accumulated totals (existing + new - removed). The loader only knows about
                # new_files_paths, so its counts must not overwrite the accumulated values.
                self.index.is_fetching = False
                stats_without_counts = {
                    k: v
                    for k, v in datasource_remote_stats.items()
                    if k not in (BaseDatasourceLoader.DOCUMENTS_COUNT_KEY, BaseDatasourceLoader.TOTAL_DOCUMENTS_KEY)
                }
                self.index.processing_info = {
                    **(self.index.processing_info or {}),
                    **stats_without_counts,
                }
                self.index.update_progress(is_fetching=False, processing_info=self.index.processing_info)

                logger.info(
                    f"IndexDatasource. Started. "
                    f"Datasource={self.datasource_name}. "
                    f"NewDocumentsCount={datasource_remote_stats.get(BaseDatasourceLoader.DOCUMENTS_COUNT_KEY)}. "
                    f"InitialCompleteState={self.index.complete_state}. "
                    f"InitialCurrentState={self.index.current_state}. "
                    f"DatasourceStats={datasource_remote_stats}"
                )

                result = self._process()
                execution_time = time.time() - start_time
                load_stats = self._persist_load_stats()
                self._load_stats_persisted = True
                self._on_process_end()
                logger.info(
                    f"IndexDatasource. Finished. "
                    f"Datasource={self.datasource_name}. "
                    f"ProcessingStats={result}. "
                    f"LoadStats={load_stats}. "
                    f"ExecutionTimeSeconds={execution_time}"
                )
                self._validate_indexing_result()
                self.index.complete_progress(self.index.current_state)
                self._create_or_update_scheduler()
                for callback in self.callbacks:
                    callback.on_complete(result)

            except IndexDeletedException as ex:
                record_exception_on_span(ex)
                logger.error(f"Stopping, index was deleted for datasource {self.index.repo_name}", exc_info=True)
                self.client.indices.delete(index=self._index_name, ignore=[400, 404])
                self._on_process_end()
                self._notify_callbacks_on_error(ex)
                return

            except GuardrailBlockedException as ex:
                record_exception_on_span(ex)
                logger.error(
                    f"Stopping, index was blocked by guardrail for datasource {self.index.repo_name}",
                    exc_info=True,
                )
                self.client.indices.delete(index=self._index_name, ignore=[400, 404])
                self.index.set_error(str(ex))
                self._on_process_end()
                self._notify_callbacks_on_error(ex)
                return

            except Exception as ex:
                record_exception_on_span(ex)
                logger.error(f"Error occurred while indexing repo {self.index.repo_name}", exc_info=True)
                if not self._load_stats_persisted:
                    self._persist_load_stats()
                self.index.set_error(str(ex))
                self._on_process_end()
                self._notify_callbacks_on_error(ex)
                raise

    def _notify_callbacks_on_error(self, ex: Exception) -> None:
        for callback in self.callbacks:
            callback.on_error(ex)

    def _remove_deleted_files_from_es(self) -> None:
        """Delete ES documents for files that were removed from the datasource (Item 1).

        Uses ``delete_by_query`` with a ``bool.should`` query so only the relevant
        source documents are removed, leaving the rest of the index intact.
        Errors are logged but never raised — a failed deletion is non-fatal.
        """
        if not self.removed_files:
            return

        try:
            if not self.client.indices.exists(index=self._index_name):
                logger.info(
                    f"FileDatasourceUpdateProcessor. ES index '{self._index_name}' does not exist; "
                    "skipping selective deletion."
                )
                return

            should_clauses = [{"term": {"metadata.source.keyword": fname}} for fname in self.removed_files]
            query = {"query": {"bool": {"should": should_clauses, "minimum_should_match": 1}}}

            self.client.delete_by_query(index=self._index_name, body=query, refresh=True)
            logger.info(
                f"FileDatasourceUpdateProcessor. RemovedFilesFromES. "
                f"Datasource={self.datasource_name}. "
                f"RemovedFiles={list(self.removed_files)}"
            )
        except Exception as e:
            logger.error(
                f"FileDatasourceUpdateProcessor. Failed to delete removed files from ES index '{self._index_name}': {e}"
            )

    def _load_es_chunks_count(self) -> int:
        """Return the total number of documents currently stored in the ES index (Item 6).

        Returns 0 if the index does not exist or if an error occurs.
        """
        try:
            if not self.client.indices.exists(index=self._index_name):
                return 0
            result = self.client.count(index=self._index_name)
            return result.get("count", 0)
        except Exception as e:
            logger.error(f"FileDatasourceUpdateProcessor. Failed to get ES doc count for '{self._index_name}': {e}")
            return 0

    def _on_process_end(self) -> None:
        """Persist the final uploaded_files / processed_files lists and ES chunk count (Items 2, 6)."""
        # Item 2: reflect the complete set of indexed files.
        self.index.uploaded_files = list(self.uploaded_files)
        self.index.processed_files = list(self.uploaded_files)

        # Item 6: sync current__chunks_state with the actual ES document count.
        self.index.current__chunks_state = self._load_es_chunks_count()

        self.index.update_progress(
            uploaded_files=self.index.uploaded_files,
            processed_files=self.index.processed_files,
            current__chunks_state=self.index.current__chunks_state,
        )
