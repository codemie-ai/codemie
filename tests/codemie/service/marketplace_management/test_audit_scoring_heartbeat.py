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

"""The scoring phases keep the run's heartbeat alive, so a big catalogue cannot outlast the reaper."""

from __future__ import annotations

import threading

import pytest

from codemie.service.marketplace_management import audit_service as svc
from codemie.service.marketplace_management.audit_service import AuditCache


def test_heartbeat_ticks_while_the_body_runs_and_stops_afterwards(monkeypatch) -> None:
    monkeypatch.setattr(svc, "AUDIT_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    touched: list[str] = []
    third_tick = threading.Event()

    def _touch(run_id: str) -> None:
        touched.append(run_id)
        if len(touched) >= 3:
            third_tick.set()

    monkeypatch.setattr(AuditCache, "_touch_heartbeat", staticmethod(_touch))

    with AuditCache()._heartbeat_while_running("run-1"):
        assert third_tick.wait(timeout=5), "no heartbeat was written while the body was running"

    settled = len(touched)
    threading.Event().wait(0.1)
    assert len(touched) == settled, "the ticker kept running after the body exited"
    assert set(touched) == {"run-1"}


def test_heartbeat_stops_when_the_body_raises(monkeypatch) -> None:
    monkeypatch.setattr(svc, "AUDIT_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(AuditCache, "_touch_heartbeat", staticmethod(lambda run_id: None))
    before = {t.name for t in threading.enumerate()}

    with pytest.raises(RuntimeError):
        with AuditCache()._heartbeat_while_running("run-2"):
            raise RuntimeError("scoring blew up")

    assert {t.name for t in threading.enumerate() if t.name.startswith("marketplace-audit-heartbeat")} <= before


def test_run_audit_scores_inside_the_heartbeat(monkeypatch) -> None:
    """The engine call sits inside the ticker's scope, not before or after it."""
    import inspect

    source = inspect.getsource(AuditCache.run_audit)
    scope = source.index("with self._heartbeat_while_running(run_id):")
    engine = source.index("= Engine(cfg).audit(")
    similarity = source.index("= self._run_similarity_phase(")
    persist = source.index("self._persist(\n")

    assert scope < engine < similarity < persist
