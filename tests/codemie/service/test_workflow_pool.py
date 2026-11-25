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

import collections
import hashlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_pool_state():
    """Reset WorkflowPool class-level state between tests."""
    from codemie.service.workflow_pool import WorkflowPool

    original_initialized = WorkflowPool._initialized
    original_instance = WorkflowPool._instance
    # Reset to a fresh dict so tests don't bleed into each other
    WorkflowPool._pools = {}
    yield
    WorkflowPool._initialized = original_initialized
    WorkflowPool._instance = original_instance
    WorkflowPool._pools = {}


def _make_config(
    yaml_config: str = "yaml: value", pool_enabled: bool = True, min_size: int = 2, max_size: int = 3
) -> MagicMock:
    cfg = MagicMock()
    cfg.yaml_config = yaml_config
    pc = MagicMock()
    pc.enabled = pool_enabled
    pc.min_size = min_size
    pc.max_size = max_size
    cfg.pool_config = pc
    return cfg


def _make_graph(slot_ids=("node_a",)):
    graph = MagicMock()
    slots = {sid: MagicMock() for sid in slot_ids}
    graph._node_slots = slots
    return graph, slots


def test_warmup_fetches_only_sql_selected_pool_enabled_workflows():
    from sqlalchemy.dialects import postgresql

    from codemie.service.workflow_service import WorkflowService

    pooled = _make_config()
    with (
        patch("codemie.repository.workflow_config_repository.WorkflowConfig.get_engine"),
        patch("codemie.repository.workflow_config_repository.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        session.exec.return_value.all.return_value = [pooled]
        assert WorkflowService.get_pool_enabled_workflow_configs() == [pooled]
        session.exec.assert_called_once()
        statement = session.exec.call_args.args[0]
    rendered = str(statement.compile(dialect=postgresql.dialect()))
    assert "WHERE (lower(pool_config ->> 'enabled') IN" in rendered
    assert "pool_config -> 'enabled' = '1'::jsonb" in rendered


class TestWorkflowPoolAcquire:
    def test_acquire_pool_miss_compiles(self):
        """Cold pool: acquire() calls _compile_for() and returns graph."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config()
        fake_graph, _ = _make_graph()

        with patch.object(pool, "_compile_for", return_value=fake_graph) as mock_compile:
            with patch("codemie.service.workflow_pool.config") as mock_cfg:
                mock_cfg.SUBWORKFLOW_POOL_ENABLED = True
                mock_cfg.SUBWORKFLOW_POOL_MAX_SIZE = 5
                result = pool.acquire("wf-1", cfg)

        mock_compile.assert_called_once_with(cfg)
        assert result is fake_graph

    def test_acquire_warm_returns_from_deque(self):
        """Warm pool: acquire() pops from deque without calling _compile_for()."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config()
        fake_graph, _ = _make_graph()

        yaml = cfg.yaml_config or ""
        config_hash = hashlib.sha256(yaml.encode()).hexdigest()[:16]
        key = ("wf-1", config_hash)
        pool._pools[key] = collections.deque([fake_graph])

        with patch.object(pool, "_compile_for") as mock_compile:
            with patch("codemie.service.workflow_pool.config") as mock_cfg:
                mock_cfg.SUBWORKFLOW_POOL_ENABLED = True
                result = pool.acquire("wf-1", cfg)

        mock_compile.assert_not_called()
        assert result is fake_graph

    def test_acquire_disabled_per_workflow_compiles_without_pooling(self):
        """pool_config.enabled=False: acquire compiles fresh, never touches pool."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config(pool_enabled=False)
        fake_graph, _ = _make_graph()

        with patch.object(pool, "_compile_for", return_value=fake_graph) as mock_compile:
            with patch("codemie.service.workflow_pool.config") as mock_cfg:
                mock_cfg.SUBWORKFLOW_POOL_ENABLED = True
                result = pool.acquire("wf-1", cfg)

        mock_compile.assert_called_once()
        # Graph acquired but not put into any pool deque
        assert len(pool._pools) == 0
        assert result is fake_graph

    def test_acquire_global_flag_disabled_compiles_without_pooling(self):
        """SUBWORKFLOW_POOL_ENABLED=False: acquire compiles fresh, never touches pool."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config(pool_enabled=True)
        fake_graph, _ = _make_graph()

        with patch.object(pool, "_compile_for", return_value=fake_graph) as mock_compile:
            with patch("codemie.service.workflow_pool.config") as mock_cfg:
                mock_cfg.SUBWORKFLOW_POOL_ENABLED = False
                pool.acquire("wf-1", cfg)

        mock_compile.assert_called_once()
        assert len(pool._pools) == 0


class TestWorkflowPoolRelease:
    def test_release_clears_slots_and_returns_to_pool(self):
        """release() clears slot delegates then pushes graph back under ceiling."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config(max_size=3)
        fake_graph, slots = _make_graph(("a", "b"))

        with patch("codemie.service.workflow_pool.config") as mock_cfg:
            mock_cfg.SUBWORKFLOW_POOL_ENABLED = True
            mock_cfg.SUBWORKFLOW_POOL_MAX_SIZE = 5
            pool.release("wf-1", cfg, fake_graph)

        for slot in slots.values():
            slot.clear.assert_called_once()

        yaml = cfg.yaml_config or ""
        config_hash = hashlib.sha256(yaml.encode()).hexdigest()[:16]
        key = ("wf-1", config_hash)
        assert fake_graph in pool._pools[key]

    def test_release_discards_at_ceiling(self):
        """release() does not push graph when pool is at ceiling."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config(max_size=2)

        existing1, _ = _make_graph()
        existing2, _ = _make_graph()
        new_graph, slots = _make_graph(("x",))

        yaml = cfg.yaml_config or ""
        config_hash = hashlib.sha256(yaml.encode()).hexdigest()[:16]
        key = ("wf-1", config_hash)
        pool._pools[key] = collections.deque([existing1, existing2])

        with patch("codemie.service.workflow_pool.config") as mock_cfg:
            mock_cfg.SUBWORKFLOW_POOL_ENABLED = True
            mock_cfg.SUBWORKFLOW_POOL_MAX_SIZE = 5
            pool.release("wf-1", cfg, new_graph)

        # Slots cleared even on discard
        slots["x"].clear.assert_called_once()
        # New graph NOT in pool
        assert new_graph not in pool._pools[key]
        assert len(pool._pools[key]) == 2

    def test_release_disabled_clears_slots_does_not_pool(self):
        """pool_config.enabled=False: release clears slots but does not return to pool."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config(pool_enabled=False)
        fake_graph, slots = _make_graph(("a",))

        with patch("codemie.service.workflow_pool.config") as mock_cfg:
            mock_cfg.SUBWORKFLOW_POOL_ENABLED = True
            mock_cfg.SUBWORKFLOW_POOL_MAX_SIZE = 5
            pool.release("wf-1", cfg, fake_graph)

        slots["a"].clear.assert_called_once()
        assert len(pool._pools) == 0


class TestWorkflowPoolConfigHash:
    def test_different_yaml_produces_different_hash(self):
        """Config hash changes when yaml_config changes → different pool key."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        cfg_a = _make_config(yaml_config="version: 1")
        cfg_b = _make_config(yaml_config="version: 2")
        graph_a, _ = _make_graph()
        graph_b, _ = _make_graph()

        with patch.object(pool, "_compile_for", side_effect=[graph_a, graph_b]):
            with patch("codemie.service.workflow_pool.config") as mock_cfg:
                mock_cfg.SUBWORKFLOW_POOL_ENABLED = True
                mock_cfg.SUBWORKFLOW_POOL_MAX_SIZE = 5
                r_a = pool.acquire("wf-1", cfg_a)
                r_b = pool.acquire("wf-1", cfg_b)

        assert r_a is graph_a
        assert r_b is graph_b


class TestWorkflowPoolSingleton:
    def test_singleton_returns_same_instance(self):
        from codemie.service.workflow_pool import WorkflowPool

        a = WorkflowPool()
        b = WorkflowPool()
        assert a is b
