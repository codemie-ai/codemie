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

import json
import logging
import time
from abc import ABC, abstractmethod
from concurrent.futures import as_completed, ThreadPoolExecutor
from typing import List, Optional
from collections import defaultdict

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import logger
from codemie.configs.logger import set_logging_info
from codemie.core.dependecies import get_elasticsearch
from codemie.datasource.callback.base_datasource_callback import DatasourceProcessorCallback
from codemie.datasource.callback.datasource_monitoring_callback import DatasourceMonitoringCallback
from codemie.datasource.datasources_config import STORAGE_CONFIG, CODE_CONFIG
from codemie.datasource.exceptions import NoChunksImportedException
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem, GuardrailEntity, GuardrailSource
from codemie.rest_api.models.index import GuardrailBlockedException, IndexInfo, IndexDeletedException
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.default_applications import ensure_application_exists
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.guardrail.guardrail_service import GuardrailService


class DatasourceBatchProcessingResult(BaseModel):
    processed_documents_count: Optional[int] = 0
    processed_document: Optional[str] = None
    processed_chunks_count: Optional[int] = 0

    @classmethod
    def new(cls, processed_documents_count: int, processed_chunks_count: int, processed_document: str):
        return cls(
            processed_documents_count=processed_documents_count,
            processed_chunks_count=processed_chunks_count,
            processed_document=processed_document,
        )


class BaseDatasourceProcessor(ABC):
    SOURCE: str = "source"
    DEFAULT_PROCESSING_BATCH_SIZE: int = 50

    user: Optional[User] = None
    index: Optional[IndexInfo] = None
    is_full_reindex: Optional[bool] = False
    is_resume_indexing: Optional[bool] = False
    is_incremental_reindex: Optional[bool] = False
    request_uuid: Optional[str] = None

    callbacks: Optional[list[DatasourceProcessorCallback]] = None

    def __init__(
        self,
        datasource_name: str,
        user: User,
        index: Optional[IndexInfo] = None,
        callbacks: Optional[list[DatasourceProcessorCallback]] = None,
        request_uuid: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
        cron_expression: Optional[str] = None,
    ):
        self.loader = None
        self.datasource_name = datasource_name
        self.index = index
        self.user = user
        self.callbacks = callbacks if callbacks else []
        self.client = ElasticSearchClient.get_client()
        self.request_uuid = request_uuid
        self.guardrail_assignments = guardrail_assignments
        self.cron_expression = cron_expression
        if user:
            set_logging_info(uuid=request_uuid, user_id=user.id, user_email=user.username)

    @property
    @abstractmethod
    def _index_name(self) -> str:
        pass

    @property
    def _processing_batch_size(self) -> int:
        # Default processing batch size if not overridden by subclasses
        return self.DEFAULT_PROCESSING_BATCH_SIZE

    def process(self):
        """
        Processes the data source by initializing the necessary components, starting the fetching process,
        and handling callbacks for completion and error scenarios.

        This method performs the following steps:
        1. Calls the `_on_process_start` method to perform any required initialization.
        2. Appends a `DatasourceMonitoringCallback` to the `callbacks` list.
        3. Starts fetching data for the given index.
        4. Initializes the loader by calling the `_init_loader` method.
        5. Processes the data by calling the `_process` method.
        6. Completes the progress by calling the `complete_progress` method on the index.
        7. Calls the `on_complete` method on each callback with the result of the `_process` method.
        8. Calls the `_on_process_end` method to perform any required cleanup.

        If an `IndexDeletedException` occurs, it logs the error, calls the `on_error` method on each callback,
        and stops the process.
        If any other exception occurs, it logs the error, sets the error state on the index,
        calls the `on_error` method on each callback, and re-raises the exception.

        Exceptions:
            IndexDeletedException: Stops the process when the index is deleted.
            Exception: Logs the error and sets the error state on the index, then re-raises the exception.

        Notes:
            - This method should not be overridden.
            - This method is intended to be called via an API.

        Raises:
            Exception: If any error occurs during processing, it is logged, and the error state is set
            on the index before re-raising the exception.
        """
        # Import is here because of circular import error
        from codemie.service.llm_service.utils import set_llm_context

        try:
            self._init_index()

            # Ensure Application exists for the project_name
            if self.index and self.index.project_name:
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
            self.index.start_fetching(is_incremental=self.is_incremental_reindex)
            self.loader = self._init_loader()
            self._on_process_start()
            if self.index and self.user:
                set_llm_context(None, self.index.project_name, self.user)
            start_time = time.time()
            datasource_remote_stats = self.loader.fetch_remote_stats()
            expected_docs_count = datasource_remote_stats.get(BaseDatasourceLoader.DOCUMENTS_COUNT_KEY)
            self.index.start_progress(
                complete_state=expected_docs_count,
                processing_info=datasource_remote_stats,
                is_incremental=self.is_incremental_reindex,
            )
            logger.info(
                f"IndexDatasource. Started. "
                f"Datasource={self.datasource_name}. "
                f"ExpectedDocumentsCount={expected_docs_count}. "
                f"InitialCompleteState={self.index.complete_state}. "
                f"InitialCurrentState={self.index.current_state}. "
                f"DatasourceStats={datasource_remote_stats}"
            )
            result = self._process()
            execution_time = time.time() - start_time
            self._on_process_end()
            logger.info(
                f"IndexDatasource. Finished. "
                f"Datasource={self.datasource_name}. "
                f"ProcessingStats={result}. "
                f"ExecutionTimeSeconds={execution_time}"
            )
            self._validate_indexing_result()
            self.index.complete_progress(self.index.current_state)

            # Create scheduler if cron_expression was provided
            self._create_or_update_scheduler()

            # Call the on_complete method of each callback
            for callback in self.callbacks:
                callback.on_complete(result)
        except IndexDeletedException as ex:
            logger.error(f"Stopping, index was deleted for datasource {self.index.repo_name}", exc_info=True)
            self.client.indices.delete(index=self._index_name, ignore=[400, 404])  # Ensure embeddings index is deleted
            self._on_process_end()  # Clear any sensitive state (e.g. OAuth tokens)
            # Call the on_error method of each callback
            for callback in self.callbacks:
                callback.on_error(ex)
            return
        except GuardrailBlockedException as ex:
            logger.error(
                f"Stopping, index was blocked by guardrail for datasource {self.index.repo_name}", exc_info=True
            )
            self.client.indices.delete(index=self._index_name, ignore=[400, 404])
            self.index.set_error(str(ex))
            self._on_process_end()
            # Call the on_error method of each callback
            for callback in self.callbacks:
                callback.on_error(ex)
            return
        except Exception as ex:
            logger.error(f"Error occurred while indexing repo {self.index.repo_name}", exc_info=True)
            self.index.set_error(str(ex))
            self._on_process_end()
            # Call the on_error method of each callback
            for callback in self.callbacks:
                callback.on_error(ex)
            raise

    def _create_or_update_scheduler(self, cron_expression: Optional[str] = None):
        """
        Create, update, or delete scheduler setting for automatic datasource reindexing.

        This method is called after successful datasource processing to set up
        automatic reindexing on a schedule defined by the cron expression.
        If cron_expression is empty, deletes existing schedule.

        Args:
            cron_expression: Optional cron expression. If not provided, uses self.cron_expression.
                           Empty string deletes the schedule.
                           If both are None (not explicitly set), scheduler is not modified.

        Notes:
            - Can be overridden by child classes for custom scheduler behavior
            - Errors are logged but don't fail the datasource creation/update
            - If cron expression is not explicitly provided, existing scheduler is preserved
        """
        # Determine which cron expression to use
        cron_expr = cron_expression if cron_expression is not None else self.cron_expression

        # If no cron expression was explicitly set, don't modify the scheduler
        # This preserves existing schedules when reindexing is triggered by cron
        if cron_expr is None:
            return

        if not self.index or not self.index.id:
            return

        from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

        try:
            result = SchedulerSettingsService.handle_schedule(
                user_id=self.user.id,
                project_name=self.index.project_name,
                resource_id=self.index.id,
                resource_name=self.index.repo_name,
                cron_expression=cron_expr,
            )
            if result:
                logger.info(f"Scheduler created/updated for datasource {self.index.id}")
            elif cron_expr is not None:
                logger.info(f"Scheduler deleted for datasource {self.index.id}")
        except Exception as e:
            logger.error(f"Failed to update scheduler for datasource {self.index.id}: {e}", exc_info=True)

    def reprocess(self):
        """
        Reprocesses the data source by first performing cleanup operations and then calling the `process` method.

        This method performs the following steps:
        1. Calls the `_cleanup_data` method to perform any necessary data cleanup.
        2. Calls the `process` method to reprocess the data source.

        Notes:
            - This method relies on the `process` method to handle the actual processing logic.
            - Ensure that the `_cleanup_data` method is properly defined to avoid any unexpected behavior.
            - Subclasses should not override this method.

        Exceptions:
            - Any exceptions raised by the `_cleanup_data` or `process` methods will propagate up the call stack.
        """
        self.is_full_reindex = True
        self._cleanup_data()
        self.process()

    def resume(self):
        """
        Resumes processing of a partially processed data source.

        This method continues indexing from where it left off by:
        1. Checking if index entries for document chunks already exist.
        2. Skipping previously processed chunks.
        3. Processing only the remaining unindexed chunks.

        Steps:
        1. Sets the indexing mode to "resume".
        2. Calls the `process` method to handle the actual data processing.

        Notes:
        - This method should not be overridden by subclasses.
        - It relies on the `process` method for the core processing logic.

        Raises:
            Any exceptions that may occur during the `process` method execution.
        """
        self.is_resume_indexing = True
        self.process()

    def incremental_reindex(self):
        """
        Performs incremental reindexing.

        This method distinguish incremental reindex as a separate flow.
        It sets is_incremental_reindex flag to True.
        This flag is used to call _cleanup_data_for_incremental_reindex,
        where additional logic can be placed, if needed

        Steps:
        1. Sets the indexing mode to "incremental".
        2. Calls the `process` method to handle the actual data processing.

        Notes:
        - This method should not be overridden by subclasses.
        - It relies on the `process` method for the core processing logic.

        Raises:
            Any exceptions that may occur during the `process` method execution.
        """
        self.is_incremental_reindex = True
        self.process()

    def _cleanup_data(self):
        """
        Method deletes existing data in index.
        Override this method if you need additional clean-up for datasource.
        """
        try:
            self.client.indices.delete(index=self._index_name)
            logger.info(f"Successfully deleted index with data: {self._index_name}")
        except Exception as e:
            logger.error(f"Failed deleting index with data: {e}")

    def _cleanup_data_for_incremental_reindex(self, docs_to_be_indexed: list[Document]):
        """
        Override this method if removing of the outdated documents from the index is needed.
        Their updated versions, along with the new documents, come as `docs_to_be_indexed` parameter,
        assuming of course that the loader takes into account the the timestamp of the documents.
        """
        pass  # This method can be overridden by subclasses

    @abstractmethod
    def _init_loader(self) -> BaseDatasourceLoader:
        """
        Initializes and returns a loader that will be used in the datasource.

        This method is abstract and should be implemented by subclasses to return an appropriate loader instance
        based on the specific requirements of the datasource.

        The loader initialization typically involves:
        - Obtaining necessary credentials and configuration settings.
        - Creating and returning a loader instance specific to the datasource type.

        Examples of loader initialization in subclasses:
        1. For a Git-based datasource:
            - Credentials are retrieved using a settings service.
            - A `GitBatchLoader` instance is created using the repository and credentials.

        2. For a Confluence-based datasource:
            - A `ConfluenceLoader` instance is created using the URL, token, CQL, and cloud status.

        Returns:
            An instance of a BaseDatasourceLoader appropriate for the datasource.

        Notes:
            - Subclasses must implement this method to provide the specific loader initialization logic.
            - Ensure that all necessary credentials and configuration settings are correctly obtained and used.

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _init_index(self):
        """
        Initializes an index to track processing information.

        This is an abstract method that must be implemented by subclasses to initialize an index.
        The index is used to track the progress and status of document processing and is a subclass of
        the `IndexInfo` class.

        Notes:
            - Subclasses must implement this method to provide the specific logic for initializing the index.
            - The initialized index should be an instance of a class that extends `IndexInfo`.

        Exceptions:
            - NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    def _on_process_start(self):
        """
        Stub method to handle the start of a process.

        This method is intended to be overridden by subclasses to
        provide custom behavior during the start of the process.
        The default implementation does nothing.

        Returns:
            None
        """
        pass  # This method can be overridden by subclasses

    def _on_process_end(self):
        """
        Stub method to handle the end of a process.

        This method is intended to be overridden by subclasses to provide custom behavior during the end of the process.
        The default implementation does nothing.

        Returns:
            None
        """
        pass  # This method can be overridden by subclasses

    def _validate_indexing_result(self):
        """
        Validates that the indexing process produced a usable result.

        This method is called after processing completes but before marking the datasource as completed.
        The default implementation checks that at least one chunk was imported.

        Subclasses can override this method to implement custom validation logic for specific datasource types.

        Raises:
            NoChunksImportedException: If no chunks were imported during indexing.

        Notes:
            - This method can be overridden by subclasses to provide custom validation logic.
            - The default implementation ensures that `current__chunks_state > 0`.
        """
        if self.index.current__chunks_state == 0:
            logger.error(
                f"IndexDatasource. NoChunksImported. "
                f"Datasource={self.datasource_name}. "
                f"ProcessedDocuments={self.index.current_state}. "
                f"ImportedChunks={self.index.current__chunks_state}"
            )
            raise NoChunksImportedException(
                datasource_name=self.datasource_name, processed_documents=self.index.current_state
            )

    def _assign_and_sync_guardrails(self):
        if not self.guardrail_assignments or not self.index or not self.index.id or not self.user:
            return

        GuardrailService.sync_guardrail_assignments_for_entity(
            user=self.user,
            entity_type=GuardrailEntity.KNOWLEDGEBASE,
            entity_id=self.index.id,
            entity_project_name=self.index.project_name,
            guardrail_assignments=self.guardrail_assignments,
        )

    def _split_documents(self, docs: list[Document]) -> dict[str, list[Document]]:
        """
        Splits a list of documents into smaller chunks and organizes them into a dictionary.

        This method performs the following steps:
        1. Iterates over each document in the provided list.
        2. Uses a text splitter to split the document's content into smaller chunks.
        3. Assigns metadata to each chunk, including a unique identifier if the document is split into multiple chunks.
        4. Processes each chunk and adds it to a list associated with the document's key.
        5. Skips a document if it doesn't have any chunks.
        6. Returns a dictionary where the keys are document identifiers and the values are lists of document chunks.

        Parameters:
            docs (list[Document]): The list of documents to be split into chunks.

        Returns:
            dict[str, list[Document]]: A dictionary with document identifiers
            as keys and lists of document chunks as values.

        Notes:
            - Ensure that the `_get_splitter` and `_process_chunk` methods are properly defined.
            - The document key is derived from the "file_path" or "source" metadata of the document.
            - Metadata is copied and adjusted for each chunk to include a unique chunk number if necessary.
            - Subclasses may override this behavior to pre-process list of documents.

        Exceptions:
            - Any exceptions raised during document splitting or chunk processing will propagate up the call stack.
        """
        documents_dict: dict[str, list[Document]] = defaultdict(list)
        for document in docs:
            split_chunks = self._get_splitter(document).split_text(document.page_content)
            chunk_list = []
            for chunk_number, chunk in enumerate(split_chunks, start=1):
                chunk_metadata = document.metadata.copy()
                if len(split_chunks) > 1:
                    chunk_metadata["chunk_num"] = chunk_number
                chunk_list.append(self._process_chunk(chunk, chunk_metadata, document))
            document_key = document.metadata.get("file_path", document.metadata.get(self.SOURCE))
            # Fix: append to existing list if key exists
            documents_dict[document_key].extend(chunk_list)
        for callback in self.callbacks:
            callback.on_split_documents(docs)
        return documents_dict

    def _process(self) -> int:
        total_documents_count = self._load_and_process_documents(
            loader=self.loader, batch_size=self._processing_batch_size, index=self.index
        )
        return total_documents_count

    def _process_chunk(self, chunk: str, chunk_metadata, _document: Document) -> Document:
        return Document(page_content=chunk, metadata=chunk_metadata)

    def _load_and_process_documents(
        self,
        loader: BaseLoader,
        index: IndexInfo,
        batch_size: int,
    ) -> int:
        """
        Loads and processes documents in batches using the provided loader and index information.

        This method performs the following steps:
        1. Retrieves the embeddings model name using the index's embeddings model.
        2. Obtains the store by index name and embeddings model.
        3. Ensures the index is created if it does not already exist.
        4. Iteratively loads documents from the loader in a lazy fashion.
        5. Processes documents in batches of the specified size.
        6. Returns the total number of documents processed.

        Parameters:
            loader (BaseLoader): The loader used to load documents.
            index (IndexInfo): The index information used for processing documents.
            batch_size (int): The number of documents to process in each batch.

        Returns:
            int: The total number of documents processed.

        Notes:
            - This method should not be overridden or called directly by subclasses.
            - Ensure that the loader implements a `lazy_load` method to load documents iteratively.
            - The `_process_batch` method is used to process each batch of documents.

        Exceptions:
            - Any exceptions raised during document loading or processing will propagate up the call stack.
        """
        embeddings_model = llm_service.get_embedding_deployment_name(self.index.embeddings_model)
        store = self._get_store_by_index(self._index_name, embeddings_model)
        store._store._create_index_if_not_exists()
        docs_batch = []
        loaded_docs = 0
        for doc in loader.lazy_load():
            docs_batch.append(doc)
            if len(docs_batch) >= batch_size:
                loaded_docs += self._process_batch(docs=docs_batch, index=index, store=store)
                docs_batch.clear()

        if docs_batch:
            loaded_docs += self._process_batch(docs=docs_batch, index=index, store=store)
        return loaded_docs

    def _process_batch(self, docs: list[Document], index: IndexInfo, store) -> int:
        """
        Processes a batch of documents by splitting them into chunks and indexing them using a thread pool.

        This method performs the following steps:
        1. Logs the start of the batch processing along with the number of files.
        2. Calls _cleanup_data_for_incremental_reindex if is_incremental_reindex is set.
        3. Splits the documents into chunks using the `_split_documents` method.
        4. Logs the number of documents and chunks produced after splitting.
        5. Uses a `ThreadPoolExecutor` to process each document's chunks in parallel.
        6. Submits tasks to the executor to process each document source and its chunks.
        7. Waits for all tasks to complete and logs the result for each document source.
        8. Returns the number of documents processed after splitting.

        Parameters:
            docs (list[Document]): The list of documents to be processed.
            index (IndexInfo): The index information used for processing documents.
            store: The store used for indexing documents.

        Returns:
            int: The number of documents processed after splitting.

        Notes:
            - This method should not be overridden or called directly by subclasses.
            - Ensure that the `_split_documents` and `_process_document` methods are properly defined.
            - Uses a thread pool to parallelize the processing of document chunks.

        Exceptions:
            - Any exceptions raised during document processing will propagate up the call stack.
        """
        logger.info(f"IndexDatasource. ProcessBatch. Datasource={self.datasource_name}. FilesCount={len(docs)}")

        if self.is_incremental_reindex:
            self._cleanup_data_for_incremental_reindex(docs)

        split_documents_dict = self._split_documents(docs)
        split_documents_count = len(split_documents_dict.keys())
        total_chunks = sum(len(chunks) for chunks in split_documents_dict.values())
        logger.info(
            f"IndexDatasource. SplitFiles. "
            f"Datasource={self.datasource_name}. "
            f"SourceDocumentsCount={split_documents_count}. "
            f"TotalChunksCount={total_chunks}. "
            f"UniqueSourceKeys={list(split_documents_dict.keys())[:5]}..."
        )

        split_documents_dict = self._apply_guardrails_for_dict(split_documents_dict)

        with ThreadPoolExecutor(max_workers=STORAGE_CONFIG.indexing_threads_count) as executor:
            futures = []
            for document_source, chunks in split_documents_dict.items():
                logger.info(
                    f"IndexDatasource. SubmittingBatch. "
                    f"Datasource={self.datasource_name}. "
                    f"Source={document_source}. "
                    f"ChunksCount={len(chunks)}"
                )
                futures.append(
                    executor.submit(self._process_document, self.datasource_name, document_source, chunks, store)
                )

            for completed_count, future in enumerate(as_completed(futures), start=1):
                try:
                    result = future.result()
                    # Gathering stats to later push it to db
                    index.gather_stats(
                        count=result.processed_documents_count,
                        chunks_count=result.processed_chunks_count,
                        processed_document=result.processed_document,
                    )
                    logger.info(
                        f"IndexDatasource. BatchCompleted. "
                        f"Datasource={self.datasource_name}. "
                        f"Source={result.processed_document}. "
                        f"ProcessedDocumentsCount={result.processed_documents_count}. "
                        f"ProcessedChunksCount={result.processed_chunks_count}. "
                        f"CurrentState={index.current_state}. "
                        f"CompleteState={index.complete_state}. "
                        f"Progress={index.current_state}/{index.complete_state}"
                    )
                except TimeoutError:
                    logger.error(
                        f"IndexDatasource. TimeoutProcessingDocument. "
                        f"Datasource={self.datasource_name}. "
                        f"Timeout=300s"
                    )
                except Exception as e:
                    logger.error(
                        f"IndexDatasource. ErrorProcessingBatch. Datasource={self.datasource_name}. Error={str(e)}"
                    )
                # Periodically commit stats mid-batch to refresh update_date and prevent
                # the stale indexing watchdog from declaring an active job as stale.
                self._try_heartbeat_commit(index, completed_count)
            self._try_commit_stats(index)
        return split_documents_count

    def _try_commit_stats(self, index: IndexInfo) -> None:
        try:
            index.commit_stats()
        except IndexDeletedException:
            raise
        except Exception as e:
            logger.error(f"IndexDatasource. CommitStatsFailed. Datasource={self.datasource_name}. Error={e}")

    def _try_heartbeat_commit(self, index: IndexInfo, completed_count: int) -> None:
        if completed_count % STORAGE_CONFIG.indexing_heartbeat_interval == 0:
            self._try_commit_stats(index)

    def _apply_guardrails_for_dict(self, split_documents_dict: dict[str, list[Document]]):
        index, guardrails = self._validate_index_and_get_guardrails_for_index()
        if not index or not guardrails:
            return split_documents_dict

        total_chunks = sum(len(documents) for documents in split_documents_dict.values())
        logger.info(f"Applying {len(guardrails)} guardrail(s) to {total_chunks} chunks")

        for documents in split_documents_dict.values():
            self._apply_guardrails_to_documents(documents, index, guardrails)

        return split_documents_dict

    def _apply_guardrails_for_documents(self, documents: list[Document]):
        index, guardrails = self._validate_index_and_get_guardrails_for_index()
        if not index or not guardrails:
            return documents

        logger.info(f"Applying {len(guardrails)} guardrail(s) to documents")
        self._apply_guardrails_to_documents(documents, index, guardrails)
        return documents

    def _validate_index_and_get_guardrails_for_index(self):
        """
        Retrieves validated index and effective guardrails for the index.
        """
        if not self.index or not self.index.id:
            logger.error("index and index.id are required for guardrail processing.")
            return None, None

        # Import here to avoid circular imports
        from codemie.service.guardrail.guardrail_service import GuardrailService

        guardrails = GuardrailService.get_effective_guardrails_for_entity(
            GuardrailEntity.KNOWLEDGEBASE,
            self.index.id,
            self.index.project_name,
            GuardrailSource.INPUT,
        )

        return self.index, guardrails

    def _apply_guardrails_to_documents(self, documents: list[Document], index: IndexInfo, guardrails):
        """
        Applies guardrails to a list of documents, modifying their page_content in place.

        Raises GuardrailBlockedException if any content is blocked by guardrails.
        """
        # Import here to avoid circular imports
        from codemie.service.guardrail.guardrail_service import GuardrailService

        for document in documents:
            guardrailed_text, blocked_reasons = GuardrailService.apply_guardrails_for_entity(
                entity_type=GuardrailEntity.KNOWLEDGEBASE,
                entity_id=str(index.id),
                project_name=index.project_name,
                input=document.page_content,
                source=GuardrailSource.INPUT,
                guardrails=guardrails,
            )

            if blocked_reasons:
                error_msg = (
                    f"Input blocked by guardrails. Reasons: {json.dumps(blocked_reasons, indent=2, default=str)}"
                )
                logger.error(
                    f"Guardrail blocked content during indexing. "
                    f"Datasource={self.datasource_name}, "
                    f"IndexID={index.id}, "
                    f"{error_msg}"
                )
                raise GuardrailBlockedException(error_msg)

            document.page_content = str(guardrailed_text)

    @retry(
        stop=stop_after_attempt(STORAGE_CONFIG.indexing_max_retries),
        wait=wait_exponential(
            multiplier=2,
            min=STORAGE_CONFIG.indexing_error_retry_wait_min_seconds,
            max=STORAGE_CONFIG.indexing_error_retry_wait_max_seconds,
        ),
        retry=retry_if_exception_type(Exception),
        reraise=True,
        before_sleep=before_sleep_log(logger, logging.ERROR, exc_info=True),
    )
    def _process_document(
        self, datasource_name: str, source: str, chunks: List[Document], store: VectorStore
    ) -> DatasourceBatchProcessingResult:
        """
        Processes a single document by storing its chunks and updating the index progress.

        This method performs the following steps:
        1. Stores the document chunks in the specified VectorStore.
        2. Returns a success result.

        Parameters:
            datasource_name (str): The name of the datasource.
            source (str): The source identifier of the document.
            chunks (List[Document]): A list of document chunks to be processed.
            store (VectorStore): The store used for storing document chunks.

        Returns:
            DatasourceBatchProcessingResult: A success result indicating the document was processed successfully.

        Notes:
            - This method should not be overridden or called directly by subclasses.
            - The method is decorated with a retry mechanism to handle transient failures.
            - The retry mechanism uses exponential backoff with configurable parameters.
            - Ensure that the `_store_document_chunks` method is properly defined.

        Exceptions:
            - Any exceptions raised during document processing are handled by the retry mechanism.
            - If all retry attempts fail, the exception will be propagated up the call stack.
        """
        if self.is_resume_indexing:
            chunk_sources = [doc.metadata['source'] for doc in chunks]
            completed_chunks = self.index.get_completed_chunks(chunk_sources)
            chunks_to_process = [doc for doc in chunks if doc.metadata['source'] not in completed_chunks]
        else:
            chunks_to_process = chunks

        if len(chunks_to_process) > 0:
            self._store_document_chunks(
                datasource_name=datasource_name, source=source, chunks=chunks_to_process, store=store
            )

        return DatasourceBatchProcessingResult.new(
            processed_documents_count=1,
            processed_chunks_count=len(chunks_to_process),
            processed_document=source,
        )

    @classmethod
    def _store_document_chunks(cls, datasource_name: str, source: str, chunks: List[Document], store: VectorStore):
        """
        Stores document chunks in the specified VectorStore in batches.

        This method performs the following steps:
        1. Logs the start of the chunk indexing process, including the datasource, source, and number of chunks.
        2. Defines the batch size for processing chunks.
        3. Iterates through the chunks in sub-batches of the defined batch size and adds them to the store.
        4. Logs the sub-batch processing details.
        5. Logs the completion of the chunk indexing process, including the execution time.

        Parameters:
            datasource_name (str): The name of the datasource.
            source (str): The source identifier of the document.
            chunks (List[Document]): A list of document chunks to be stored.
            store (VectorStore): The store used for storing document chunks.

        Returns:
            None

        Notes:
            - Ensure that the `add_documents` method of the VectorStore is properly defined.
            - The batch size and bulk parameters are configurable via the `STORAGE_CONFIG`.

        Exceptions:
            - Any exceptions raised during the document chunk storage will propagate up the call stack.
        """
        start_time = time.time()
        logger.debug(
            f"IndexingChunks. Started. Datasource={datasource_name}. Source={source}. ChunksCount={len(chunks)}"
        )

        batch_size = STORAGE_CONFIG.embeddings_max_docs_count  # Define the batch size
        total_batches = (len(chunks) + batch_size - 1) // batch_size  # Calculate total number of batches

        for batch_num, i in enumerate(range(0, len(chunks), batch_size), start=1):
            sub_batch = chunks[i : i + batch_size]
            chunk_range_end = min(i + batch_size, len(chunks))

            logger.debug(
                f"IndexingChunks. SubBatch. Datasource={datasource_name}. Source={source}. "
                f"Batch={batch_num}/{total_batches}. ChunkRange={i}-{chunk_range_end}. "
                f"SubBatchSize={len(sub_batch)}"
            )

            sub_batch_start = time.time()
            store.add_documents(
                documents=sub_batch,
                create_index_if_not_exists=False,
                refresh_indices=False,
                bulk_kwargs={
                    "max_chunk_bytes": STORAGE_CONFIG.indexing_bulk_max_chunk_bytes,
                    "max_retries": STORAGE_CONFIG.indexing_max_retries,
                },
            )
            sub_batch_time = time.time() - sub_batch_start

            logger.debug(
                f"IndexingChunks. SubBatchCompleted. Datasource={datasource_name}. Source={source}. "
                f"Batch={batch_num}/{total_batches}. Time={sub_batch_time:.2f}s"
            )
        execution_time = time.time() - start_time
        logger.debug(
            f"IndexingChunks. Finished. "
            f"Datasource={datasource_name}. "
            f"Source={source}. "
            f"ChunksCount={len(chunks)}. "
            f"TotalBatches={total_batches}. "
            f"ExecutionTimeSeconds={execution_time:.2f}"
        )

    @staticmethod
    def _get_store_by_index(index_name: str, embeddings_model: str) -> VectorStore:
        return get_elasticsearch(index_name, embeddings_model)

    @classmethod
    def _get_splitter(cls, document: Document = None) -> RecursiveCharacterTextSplitter:
        if document:
            file_type = document.metadata.get("file_type")
            if file_type in CODE_CONFIG.extension_to_language:
                return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                    encoding_name="o200k_base",
                    separators=RecursiveCharacterTextSplitter.get_separators_for_language(
                        CODE_CONFIG.extension_to_language[file_type]
                    ),
                    chunk_size=CODE_CONFIG.tokens_size_limit,
                    disallowed_special={},
                    chunk_overlap=CODE_CONFIG.chunk_overlap,
                )
        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="o200k_base",
            chunk_size=CODE_CONFIG.chunk_size,
            disallowed_special={},
            chunk_overlap=CODE_CONFIG.chunk_overlap,
        )
