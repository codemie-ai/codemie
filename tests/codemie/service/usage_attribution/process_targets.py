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

"""Entry points of computation processes for the tests: spawned processes import them by name."""

from __future__ import annotations

import os
import time

from codemie.service.usage_attribution.analysis import serve_job
from codemie.service.usage_attribution.constants import WORKER_MEMORY_EXIT_CODE


def _result(job: dict) -> dict:
    return {"sections": {}, "inputs_blob": None, "meta": {"days": job["window"]["days"]}}


def slow_for_long_ranges(conn, job: dict) -> None:
    """A 30-day job runs far longer than the test timeout; a 7-day one finishes at once."""

    def run(j: dict) -> dict:
        time.sleep(30.0 if j["window"]["days"] > 20 else 0.1)
        return _result(j)

    serve_job(conn, run, job)


def failing(conn, job: dict) -> None:
    def run(_job: dict) -> dict:
        raise LookupError("no such index")

    serve_job(conn, run, job)


def out_of_memory(_conn, _job: dict) -> None:
    os._exit(WORKER_MEMORY_EXIT_CODE)  # what the memory watchdog does


def crashing(_conn, _job: dict) -> None:
    os._exit(3)
