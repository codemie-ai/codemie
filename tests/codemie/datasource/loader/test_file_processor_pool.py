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
from concurrent.futures.process import BrokenProcessPool
from unittest.mock import MagicMock, patch


def _noop(*args, **kwargs):
    return "result"


@pytest.fixture(autouse=True)
def reset_pool_state():
    """Reset FileProcessPoolManager class-level state between tests."""
    from codemie.datasource.loader.file_processor_pool import FileProcessPoolManager

    original_initialized = FileProcessPoolManager._initialized
    original_executor = FileProcessPoolManager._executor
    original_instance = FileProcessPoolManager._instance
    original_flag = FileProcessPoolManager._in_pool_worker
    yield
    FileProcessPoolManager._in_pool_worker = original_flag
    FileProcessPoolManager._initialized = original_initialized
    FileProcessPoolManager._executor = original_executor
    FileProcessPoolManager._instance = original_instance


class TestMaybePoolSubmit:
    def test_inline_when_disabled(self):
        """When ENABLE_FILE_MULTIPROCESSING=False, fn is called directly."""
        from codemie.datasource.loader.file_processor_pool import maybe_pool_submit

        called_with = []

        def fn(a, b):
            called_with.append((a, b))
            return "inline"

        with patch("codemie.datasource.loader.file_processor_pool.config") as mock_config:
            mock_config.ENABLE_FILE_MULTIPROCESSING = False
            result = maybe_pool_submit(fn, 1, b=2)

        assert result == "inline"
        assert called_with == [(1, 2)]

    def test_inline_when_not_initialized(self):
        """When pool is not initialized, fn is called directly."""
        from codemie.datasource.loader.file_processor_pool import maybe_pool_submit, FileProcessPoolManager

        FileProcessPoolManager._initialized = False

        def fn():
            return "inline"

        with patch("codemie.datasource.loader.file_processor_pool.config") as mock_config:
            mock_config.ENABLE_FILE_MULTIPROCESSING = True
            result = maybe_pool_submit(fn)

        assert result == "inline"

    def test_broken_pool_reinits_and_retries(self):
        """On BrokenProcessPool: reinit called, retry submitted, result returned."""
        from codemie.datasource.loader.file_processor_pool import maybe_pool_submit, FileProcessPoolManager

        FileProcessPoolManager._initialized = True

        mock_future_first = MagicMock()
        mock_future_first.result.side_effect = BrokenProcessPool("pool broken")

        mock_future_retry = MagicMock()
        mock_future_retry.result.return_value = "retry_result"

        mock_executor = MagicMock()
        mock_executor.submit.side_effect = [mock_future_first, mock_future_retry]
        FileProcessPoolManager._executor = mock_executor

        with (
            patch("codemie.datasource.loader.file_processor_pool.config") as mock_config,
            patch("codemie.datasource.loader.file_processor_pool._reinitialize_pool") as mock_reinit,
        ):
            mock_config.ENABLE_FILE_MULTIPROCESSING = True
            result = maybe_pool_submit(_noop, "arg1")

        mock_reinit.assert_called_once()
        assert mock_executor.submit.call_count == 2
        assert result == "retry_result"

    def test_reinit_failure_raises(self):
        """If _reinitialize_pool raises, the exception propagates."""
        from codemie.datasource.loader.file_processor_pool import maybe_pool_submit, FileProcessPoolManager

        FileProcessPoolManager._initialized = True

        mock_future = MagicMock()
        mock_future.result.side_effect = BrokenProcessPool("pool broken")

        mock_executor = MagicMock()
        mock_executor.submit.return_value = mock_future
        FileProcessPoolManager._executor = mock_executor

        with (
            patch("codemie.datasource.loader.file_processor_pool.config") as mock_config,
            patch(
                "codemie.datasource.loader.file_processor_pool._reinitialize_pool",
                side_effect=RuntimeError("reinit failed"),
            ),
        ):
            mock_config.ENABLE_FILE_MULTIPROCESSING = True
            with pytest.raises(RuntimeError, match="reinit failed"):
                maybe_pool_submit(_noop)

    def test_retry_failure_raises(self):
        """If reinit succeeds but retry submit raises, exception propagates."""
        from codemie.datasource.loader.file_processor_pool import maybe_pool_submit, FileProcessPoolManager

        FileProcessPoolManager._initialized = True

        mock_future_first = MagicMock()
        mock_future_first.result.side_effect = BrokenProcessPool("pool broken")

        mock_future_retry = MagicMock()
        mock_future_retry.result.side_effect = OSError("worker killed")

        mock_executor = MagicMock()
        mock_executor.submit.side_effect = [mock_future_first, mock_future_retry]
        FileProcessPoolManager._executor = mock_executor

        with (
            patch("codemie.datasource.loader.file_processor_pool.config") as mock_config,
            patch("codemie.datasource.loader.file_processor_pool._reinitialize_pool"),
        ):
            mock_config.ENABLE_FILE_MULTIPROCESSING = True
            with pytest.raises(OSError, match="worker killed"):
                maybe_pool_submit(_noop)

    def test_non_pool_exception_raises_unchanged(self):
        """Non-BrokenProcessPool exceptions from the future propagate directly."""
        from codemie.datasource.loader.file_processor_pool import maybe_pool_submit, FileProcessPoolManager

        FileProcessPoolManager._initialized = True

        mock_future = MagicMock()
        mock_future.result.side_effect = ValueError("bad input")

        mock_executor = MagicMock()
        mock_executor.submit.return_value = mock_future
        FileProcessPoolManager._executor = mock_executor

        with patch("codemie.datasource.loader.file_processor_pool.config") as mock_config:
            mock_config.ENABLE_FILE_MULTIPROCESSING = True
            with pytest.raises(ValueError, match="bad input"):
                maybe_pool_submit(_noop)


class TestReinitializePool:
    def test_calls_shutdown_then_initialize(self):
        from codemie.datasource.loader import file_processor_pool

        with (
            patch.object(file_processor_pool.file_process_pool, "shutdown") as mock_shutdown,
            patch.object(file_processor_pool.file_process_pool, "initialize") as mock_initialize,
            patch("codemie.datasource.loader.file_processor_pool.config") as mock_config,
        ):
            mock_config.FILE_DATASOURCE_MULTIPROCESSING_MAX_WORKERS = 2
            mock_config.FILE_MULTIPROCESSING_MAX_EXECUTED_TASK_PER_WORKER = 100
            file_processor_pool._reinitialize_pool()

        mock_shutdown.assert_called_once_with(wait=False)
        mock_initialize.assert_called_once_with(
            max_workers=2,
            max_tasks_per_child=100,
            force=True,
        )


class TestWorkerFlag:
    def test_flag_set_runs_inline_without_touching_executor(self):
        from codemie.datasource.loader import file_processor_pool
        from codemie.datasource.loader.file_processor_pool import FileProcessPoolManager

        FileProcessPoolManager._initialized = True
        FileProcessPoolManager._in_pool_worker = True

        with (
            patch("codemie.datasource.loader.file_processor_pool.config") as mock_config,
            patch.object(FileProcessPoolManager, "get_executor") as mock_get_executor,
        ):
            mock_config.ENABLE_FILE_MULTIPROCESSING = True
            result = file_processor_pool.maybe_pool_submit(lambda a, b: (a, b), 1, b=2)

        assert result == (1, 2)
        mock_get_executor.assert_not_called()

    def test_flag_unset_keeps_pool_path(self):
        from codemie.datasource.loader import file_processor_pool
        from codemie.datasource.loader.file_processor_pool import FileProcessPoolManager

        FileProcessPoolManager._initialized = True
        FileProcessPoolManager._in_pool_worker = False

        mock_future = MagicMock()
        mock_future.result.return_value = "pooled"
        mock_executor = MagicMock()
        mock_executor.submit.return_value = mock_future
        FileProcessPoolManager._executor = mock_executor

        with patch("codemie.datasource.loader.file_processor_pool.config") as mock_config:
            mock_config.ENABLE_FILE_MULTIPROCESSING = True
            result = file_processor_pool.maybe_pool_submit(_noop, "x")

        assert result == "pooled"
        mock_executor.submit.assert_called_once()

    def test_initializer_sets_flag_before_configuring_pyroscope(self):
        from codemie.datasource.loader import file_processor_pool
        from codemie.datasource.loader.file_processor_pool import FileProcessPoolManager

        FileProcessPoolManager._in_pool_worker = False
        seen = []

        def fake_configure(settings):
            seen.append((FileProcessPoolManager._in_pool_worker, settings))

        with patch("codemie.datasource.loader.file_processor_pool.configure_pyroscope", side_effect=fake_configure):
            file_processor_pool._pool_worker_initializer({"process_type": "subprocess"})

        assert seen == [(True, {"process_type": "subprocess"})]
        assert FileProcessPoolManager._in_pool_worker is True

    def test_initializer_sets_flag_when_pyroscope_raises(self):
        from codemie.datasource.loader import file_processor_pool
        from codemie.datasource.loader.file_processor_pool import FileProcessPoolManager

        FileProcessPoolManager._in_pool_worker = False

        with patch(
            "codemie.datasource.loader.file_processor_pool.configure_pyroscope",
            side_effect=RuntimeError("pyroscope down"),
        ):
            with pytest.raises(RuntimeError, match="pyroscope down"):
                file_processor_pool._pool_worker_initializer({})

        assert FileProcessPoolManager._in_pool_worker is True

    def test_initialize_passes_worker_initializer(self):
        from codemie.datasource.loader import file_processor_pool

        with (
            patch("codemie.datasource.loader.file_processor_pool.ProcessPoolExecutor") as mock_executor_cls,
            patch("codemie.datasource.loader.file_processor_pool.atexit.register"),
            patch("codemie.datasource.loader.file_processor_pool.multiprocessing.set_start_method"),
        ):
            file_processor_pool.FileProcessPoolManager.initialize(max_workers=2, max_tasks_per_child=5, force=True)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["initializer"] is file_processor_pool._pool_worker_initializer
        assert kwargs["initargs"] == ({"process_type": "subprocess", "process_pool": "FileProcessPoolManager"},)


class TestFileParsingTimeoutError:
    def test_message_and_pickle_round_trip(self):
        import pickle

        from codemie.datasource.exceptions import FileParsingTimeoutError

        error = FileParsingTimeoutError(1800)
        restored = pickle.loads(pickle.dumps(error))

        assert str(restored) == "File parsing timed out after 1800s"
        assert restored.timeout_seconds == 1800
        assert type(restored) is FileParsingTimeoutError


class TestBoundedWait:
    @staticmethod
    def _setup_executor(*futures):
        from codemie.datasource.loader.file_processor_pool import FileProcessPoolManager

        FileProcessPoolManager._initialized = True
        mock_executor = MagicMock()
        mock_executor.submit.side_effect = list(futures)
        FileProcessPoolManager._executor = mock_executor
        return mock_executor

    @staticmethod
    def _run(timeout, *args, **kwargs):
        from codemie.datasource.loader.file_processor_pool import maybe_pool_submit

        with (
            patch("codemie.datasource.loader.file_processor_pool.config") as mock_config,
            patch("codemie.datasource.loader.file_processor_pool.FILE_CONFIG") as mock_file_config,
            patch("codemie.datasource.loader.file_processor_pool._reinitialize_pool") as mock_reinit,
        ):
            mock_config.ENABLE_FILE_MULTIPROCESSING = True
            mock_file_config.processing_timeout = timeout
            try:
                return maybe_pool_submit(_noop, *args, **kwargs), mock_reinit
            except Exception as error:
                error.mock_reinit = mock_reinit
                raise

    def test_expired_wait_raises_timeout_error_cancels_and_keeps_pool(self):
        from codemie.datasource.exceptions import FileParsingTimeoutError

        future = MagicMock()
        future.result.side_effect = TimeoutError()
        future.done.return_value = False
        self._setup_executor(future)

        with pytest.raises(FileParsingTimeoutError, match="File parsing timed out after 30s") as exc_info:
            self._run(30)

        future.result.assert_called_once_with(timeout=30)
        future.cancel.assert_called_once()
        exc_info.value.mock_reinit.assert_not_called()
        assert exc_info.value.__suppress_context__ is True

    @pytest.mark.parametrize("configured", [0, -1])
    def test_non_positive_timeout_means_no_limit(self, configured):
        future = MagicMock()
        future.result.return_value = "ok"
        self._setup_executor(future)

        result, _ = self._run(configured)

        assert result == "ok"
        future.result.assert_called_once_with(timeout=None)

    def test_late_finish_returns_result_without_cancel(self):
        future = MagicMock()
        future.result.side_effect = [TimeoutError(), "late"]
        future.done.return_value = True
        self._setup_executor(future)

        result, _ = self._run(30)

        assert result == "late"
        future.cancel.assert_not_called()

    def test_late_finish_with_worker_exception_reraises_it_unchanged(self):
        future = MagicMock()
        worker_error = OSError("disk gone")
        future.result.side_effect = [TimeoutError(), worker_error]
        future.done.return_value = True
        self._setup_executor(future)

        with pytest.raises(OSError, match="disk gone") as exc_info:
            self._run(30)

        assert exc_info.value is worker_error
        future.cancel.assert_not_called()

    def test_timeout_error_raised_by_submitted_function_is_not_a_pool_timeout(self):
        function_error = TimeoutError("socket timed out")
        future = MagicMock()
        future.result.side_effect = function_error
        future.done.return_value = True
        self._setup_executor(future)

        with pytest.raises(TimeoutError) as exc_info:
            self._run(30)

        assert exc_info.value is function_error
        future.cancel.assert_not_called()

    def test_retry_after_broken_pool_has_its_own_bounded_wait(self):
        from codemie.datasource.exceptions import FileParsingTimeoutError

        first = MagicMock()
        first.result.side_effect = BrokenProcessPool("pool broken")
        retry = MagicMock()
        retry.result.side_effect = TimeoutError()
        retry.done.return_value = False
        self._setup_executor(first, retry)

        with pytest.raises(FileParsingTimeoutError) as exc_info:
            self._run(30)

        first.result.assert_called_once_with(timeout=30)
        retry.result.assert_called_once_with(timeout=30)
        retry.cancel.assert_called_once()
        exc_info.value.mock_reinit.assert_called_once()


class TestInitializeStartMethod:
    @staticmethod
    def _initialize(max_tasks_per_child, fake_context=None):
        from codemie.datasource.loader import file_processor_pool

        with (
            patch("codemie.datasource.loader.file_processor_pool.ProcessPoolExecutor") as mock_executor_cls,
            patch("codemie.datasource.loader.file_processor_pool.atexit.register"),
            patch("codemie.datasource.loader.file_processor_pool.multiprocessing.set_start_method"),
            patch("codemie.datasource.loader.file_processor_pool.logger") as mock_logger,
        ):
            if fake_context is None:
                file_processor_pool.FileProcessPoolManager.initialize(
                    max_workers=2, max_tasks_per_child=max_tasks_per_child, force=True
                )
            else:
                with patch(
                    "codemie.datasource.loader.file_processor_pool.multiprocessing.get_context",
                    return_value=fake_context,
                ) as mock_get_context:
                    file_processor_pool.FileProcessPoolManager.initialize(
                        max_workers=2, max_tasks_per_child=max_tasks_per_child, force=True
                    )
                    mock_get_context.assert_called_once_with()
        return mock_executor_cls.call_args.kwargs, mock_logger.info.call_args.args[0]

    def test_task_limit_uses_spawn_context_and_logs_spawn(self):
        kwargs, message = self._initialize(max_tasks_per_child=100)

        assert kwargs["mp_context"].get_start_method() == "spawn"
        assert kwargs["max_tasks_per_child"] == 100
        assert "start_method=spawn" in message

    def test_no_task_limit_uses_default_context_and_logs_its_method(self):
        fake_context = MagicMock()
        fake_context.get_start_method.return_value = "forkserver"

        kwargs, message = self._initialize(max_tasks_per_child=-1, fake_context=fake_context)

        assert kwargs["mp_context"] is fake_context
        assert kwargs["max_tasks_per_child"] is None
        assert "start_method=forkserver" in message


def test_processing_timeout_is_1800_in_shipped_config():
    from codemie.datasource.datasources_config import FILE_CONFIG

    assert FILE_CONFIG.processing_timeout == 1800


def test_processing_timeout_defaults_to_1800_when_config_omits_it():
    from codemie.datasource.datasources_config import FileConfig

    assert FileConfig(chunk_size=1500, chunk_overlap=100).processing_timeout == 1800
