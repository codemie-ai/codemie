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


import contextlib
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import psutil
import pytest

from codemie.datasource.loader.file_processor_pool import _pool_worker_initializer
from tests.codemie.datasource.loader.pool_worker_helpers import nested_square, plain_initializer

REPO_ROOT = Path(__file__).resolve().parents[4]
TASK_COUNT = 5
MAX_TASKS_PER_CHILD = 2
RESULT_TIMEOUT_SECONDS = 30


def _kill_process_tree(pid: int) -> None:
    try:
        process = psutil.Process(pid)
        descendants = process.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    for victim in [*reversed(descendants), process]:
        with contextlib.suppress(psutil.NoSuchProcess):
            victim.kill()


@pytest.fixture
def spawn_pool_factory(monkeypatch):
    monkeypatch.setenv("ENABLE_FILE_MULTIPROCESSING", "true")
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    executors = []
    worker_pids = set()

    def build(initializer, initargs):
        executor = ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
            max_tasks_per_child=MAX_TASKS_PER_CHILD,
            initializer=initializer,
            initargs=initargs,
        )
        executors.append(executor)
        return executor, worker_pids

    yield build

    for pid in worker_pids:
        _kill_process_tree(pid)
    for executor in executors:
        executor.shutdown(wait=False, cancel_futures=True)


def _run_nested_tasks(executor, worker_pids):
    results = []
    for value in range(TASK_COUNT):
        future = executor.submit(nested_square, value)
        worker_pids.update(executor._processes)
        results.append(future.result(timeout=RESULT_TIMEOUT_SECONDS))
        worker_pids.update(executor._processes)
    return results


def test_nested_submits_past_task_limit_complete_with_worker_initializer(spawn_pool_factory):
    executor, worker_pids = spawn_pool_factory(_pool_worker_initializer, ({},))

    results = _run_nested_tasks(executor, worker_pids)

    assert results == [value * value for value in range(TASK_COUNT)]


def test_nested_submits_past_task_limit_hang_without_worker_initializer(spawn_pool_factory):
    executor, worker_pids = spawn_pool_factory(plain_initializer, ())

    with pytest.raises(TimeoutError):
        _run_nested_tasks(executor, worker_pids)
