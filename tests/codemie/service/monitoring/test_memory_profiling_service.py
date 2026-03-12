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

"""Tests for memory profiling service."""

import gzip
import json
import tracemalloc
from unittest.mock import Mock, patch
import pytest

from codemie.service.monitoring.memory_profiling_service import (
    MemoryProfilingService,
    MemorySnapshotData,
    MemorySnapshotStats,
    memory_profiling_service,
)


@pytest.fixture
def service():
    """Create a fresh memory profiling service instance for testing."""
    with patch('codemie.service.monitoring.memory_profiling_service.config') as mock_config:
        mock_config.CODEMIE_STORAGE_BUCKET_NAME = "test/snapshots"
        service_instance = MemoryProfilingService()
        return service_instance


@pytest.fixture
def mock_file_repository():
    """Mock file repository."""
    mock_repo = Mock()
    mock_repo.write_file = Mock()
    return mock_repo


class TestMemoryProfilingService:
    """Test suite for MemoryProfilingService."""

    def test_init(self, service):
        """Test service initialization."""
        assert service._tracking_enabled is False
        assert service._snapshot_count == 0
        assert service._owner == "test/snapshots"
        assert service._file_repository is not None

    def test_serialize_snapshot(self, service):
        """Test snapshot serialization."""
        snapshot_data = MemorySnapshotData(
            id="test-id",
            timestamp="2024-01-01T00:00:00",
            description="Test snapshot",
            current_memory_mb=10.5,
            peak_memory_mb=15.2,
            traced_memory_blocks=100,
            top_allocations=[{"location": "test.py:10", "size_mb": 5.0, "count": 10}],
        )

        result = service._serialize_snapshot(snapshot_data)

        assert isinstance(result, bytes)
        # Verify it's gzip compressed
        decompressed = gzip.decompress(result).decode('utf-8')
        data = json.loads(decompressed)
        assert data["id"] == "test-id"
        assert data["current_memory_mb"] == 10.5

    def test_start_tracking_success(self, service):
        """Test successful start of memory tracking."""
        assert service.start_tracking() is True
        assert service._tracking_enabled is True
        assert tracemalloc.is_tracing() is True
        # Cleanup
        service.stop_tracking()

    def test_start_tracking_already_enabled(self, service):
        """Test starting tracking when already enabled."""
        service.start_tracking()
        assert service.start_tracking() is False
        # Cleanup
        service.stop_tracking()

    @patch('tracemalloc.start')
    def test_start_tracking_failure(self, mock_start, service):
        """Test start tracking failure."""
        mock_start.side_effect = Exception("Test error")
        assert service.start_tracking() is False
        assert service._tracking_enabled is False

    def test_stop_tracking_success(self, service):
        """Test successful stop of memory tracking."""
        service.start_tracking()
        assert service.stop_tracking() is True
        assert service._tracking_enabled is False
        assert tracemalloc.is_tracing() is False

    def test_stop_tracking_not_enabled(self, service):
        """Test stopping tracking when not enabled."""
        assert service.stop_tracking() is False

    @patch('tracemalloc.stop')
    def test_stop_tracking_failure(self, mock_stop, service):
        """Test stop tracking failure."""
        service.start_tracking()
        mock_stop.side_effect = Exception("Test error")
        assert service.stop_tracking() is False

    def test_is_tracking(self, service):
        """Test is_tracking method."""
        assert service.is_tracking() is False
        service.start_tracking()
        assert service.is_tracking() is True
        service.stop_tracking()
        assert service.is_tracking() is False

    def test_take_snapshot_not_tracking(self, service):
        """Test taking snapshot when tracking is not enabled."""
        result = service.take_snapshot()
        assert result is None

    @patch('codemie.service.monitoring.memory_profiling_service.config')
    def test_take_snapshot_success(self, mock_config, service, mock_file_repository):
        """Test successful snapshot creation."""
        mock_config.MEMORY_PROFILING_DETAIL_LEVEL = "file"
        service._file_repository = mock_file_repository
        service.start_tracking()

        snapshot_id = service.take_snapshot(description="Test snapshot")

        assert snapshot_id is not None
        assert service._snapshot_count == 1
        mock_file_repository.write_file.assert_called_once()

        # Cleanup
        service.stop_tracking()

    @patch('codemie.service.monitoring.memory_profiling_service.config')
    def test_take_snapshot_with_metadata(self, mock_config, service, mock_file_repository):
        """Test snapshot with custom metadata."""
        mock_config.MEMORY_PROFILING_DETAIL_LEVEL = "file"
        service._file_repository = mock_file_repository
        service.start_tracking()

        metadata = {"custom_key": "custom_value"}
        snapshot_id = service.take_snapshot(description="Test", metadata=metadata)

        assert snapshot_id is not None
        # Cleanup
        service.stop_tracking()

    @patch('codemie.service.monitoring.memory_profiling_service.config')
    def test_take_snapshot_line_detail_level(self, mock_config, service, mock_file_repository):
        """Test snapshot with line-level detail."""
        mock_config.MEMORY_PROFILING_DETAIL_LEVEL = "line"
        service._file_repository = mock_file_repository
        service.start_tracking()

        snapshot_id = service.take_snapshot()

        assert snapshot_id is not None
        # Cleanup
        service.stop_tracking()

    @patch('codemie.service.monitoring.memory_profiling_service.PSUTIL_AVAILABLE', False)
    def test_take_snapshot_without_psutil(self, service, mock_file_repository):
        """Test snapshot when psutil is not available."""
        service._file_repository = mock_file_repository
        service.start_tracking()

        snapshot_id = service.take_snapshot()

        assert snapshot_id is not None
        # Cleanup
        service.stop_tracking()

    @patch('codemie.service.monitoring.memory_profiling_service.psutil')
    @patch('codemie.service.monitoring.memory_profiling_service.PSUTIL_AVAILABLE', True)
    @patch('codemie.service.monitoring.memory_profiling_service.config')
    def test_take_snapshot_with_psutil(self, mock_config, mock_psutil, service, mock_file_repository):
        """Test snapshot with psutil metrics."""
        mock_config.MEMORY_PROFILING_DETAIL_LEVEL = "file"

        # Mock psutil
        mock_process = Mock()
        mock_mem_info = Mock()
        mock_mem_info.rss = 100 * 1024 * 1024  # 100 MB
        mock_mem_info.vms = 200 * 1024 * 1024  # 200 MB
        mock_mem_info.shared = 10 * 1024 * 1024  # 10 MB
        mock_process.memory_info.return_value = mock_mem_info
        mock_process.memory_percent.return_value = 5.0

        mock_sys_mem = Mock()
        mock_sys_mem.total = 8 * 1024 * 1024 * 1024  # 8 GB
        mock_sys_mem.available = 4 * 1024 * 1024 * 1024  # 4 GB
        mock_sys_mem.percent = 50.0

        mock_psutil.Process.return_value = mock_process
        mock_psutil.virtual_memory.return_value = mock_sys_mem

        service._file_repository = mock_file_repository
        service.start_tracking()

        snapshot_id = service.take_snapshot()

        assert snapshot_id is not None
        # Cleanup
        service.stop_tracking()

    @patch('codemie.service.monitoring.memory_profiling_service.psutil')
    @patch('codemie.service.monitoring.memory_profiling_service.PSUTIL_AVAILABLE', True)
    @patch('codemie.service.monitoring.memory_profiling_service.config')
    def test_take_snapshot_high_native_memory(self, mock_config, mock_psutil, service, mock_file_repository):
        """Test snapshot with high native memory warning."""
        mock_config.MEMORY_PROFILING_DETAIL_LEVEL = "file"

        # Mock psutil with high native memory
        mock_process = Mock()
        mock_mem_info = Mock()
        mock_mem_info.rss = 200 * 1024 * 1024  # 200 MB (high)
        mock_mem_info.vms = 300 * 1024 * 1024
        mock_mem_info.shared = 0
        mock_process.memory_info.return_value = mock_mem_info
        mock_process.memory_percent.return_value = 10.0

        mock_sys_mem = Mock()
        mock_sys_mem.total = 8 * 1024 * 1024 * 1024
        mock_sys_mem.available = 4 * 1024 * 1024 * 1024
        mock_sys_mem.percent = 50.0

        mock_psutil.Process.return_value = mock_process
        mock_psutil.virtual_memory.return_value = mock_sys_mem

        service._file_repository = mock_file_repository
        service.start_tracking()

        snapshot_id = service.take_snapshot()

        assert snapshot_id is not None
        # Cleanup
        service.stop_tracking()

    @patch('tracemalloc.take_snapshot')
    def test_take_snapshot_failure(self, mock_take_snapshot, service):
        """Test snapshot failure."""
        service.start_tracking()
        mock_take_snapshot.side_effect = Exception("Test error")

        result = service.take_snapshot()

        assert result is None
        # Cleanup
        service.stop_tracking()

    def test_get_current_stats_not_tracking(self, service):
        """Test getting stats when not tracking."""
        stats = service.get_current_stats()

        assert stats is not None
        assert stats.tracking_enabled is False
        assert stats.current_memory_mb == 0.0
        assert stats.peak_memory_mb == 0.0
        assert stats.traced_memory_blocks == 0

    def test_get_current_stats_tracking(self, service):
        """Test getting stats when tracking."""
        service.start_tracking()

        stats = service.get_current_stats()

        assert stats is not None
        assert stats.tracking_enabled is True
        assert stats.current_memory_mb >= 0
        assert stats.peak_memory_mb >= 0
        assert stats.traced_memory_blocks >= 0

        # Cleanup
        service.stop_tracking()

    @patch('codemie.service.monitoring.memory_profiling_service.psutil')
    @patch('codemie.service.monitoring.memory_profiling_service.PSUTIL_AVAILABLE', True)
    def test_get_current_stats_with_psutil(self, mock_psutil, service):
        """Test getting stats with psutil metrics."""
        # Mock psutil
        mock_process = Mock()
        mock_mem_info = Mock()
        mock_mem_info.rss = 100 * 1024 * 1024
        mock_mem_info.vms = 200 * 1024 * 1024
        mock_process.memory_info.return_value = mock_mem_info

        mock_sys_mem = Mock()
        mock_sys_mem.available = 4 * 1024 * 1024 * 1024
        mock_sys_mem.percent = 50.0

        mock_psutil.Process.return_value = mock_process
        mock_psutil.virtual_memory.return_value = mock_sys_mem

        service.start_tracking()

        stats = service.get_current_stats()

        assert stats is not None
        assert stats.process_rss_mb is not None
        assert stats.process_vms_mb is not None
        assert stats.native_untracked_mb is not None

        # Cleanup
        service.stop_tracking()

    @patch('codemie.service.monitoring.memory_profiling_service.psutil')
    @patch('codemie.service.monitoring.memory_profiling_service.PSUTIL_AVAILABLE', True)
    def test_get_current_stats_psutil_failure(self, mock_psutil, service):
        """Test getting stats when psutil fails."""
        mock_psutil.Process.side_effect = Exception("Test error")

        service.start_tracking()

        stats = service.get_current_stats()

        assert stats is not None
        assert stats.process_rss_mb is None

        # Cleanup
        service.stop_tracking()

    @patch('tracemalloc.get_traced_memory')
    def test_get_current_stats_failure(self, mock_get_traced, service):
        """Test get stats failure."""
        service.start_tracking()
        mock_get_traced.side_effect = Exception("Test error")

        result = service.get_current_stats()

        assert result is None
        # Cleanup
        service.stop_tracking()

    def test_format_top_allocations(self, service):
        """Test formatting of top allocations."""
        mock_stat = Mock()
        mock_stat.size = 1024 * 1024  # 1 MB
        mock_stat.count = 10
        mock_stat.traceback = Mock()
        mock_stat.traceback.format.return_value = ["  File \"/path/to/codemie/test.py\", line 10\n    test_code()"]

        result = service._format_top_allocations([mock_stat])

        assert len(result) == 1
        assert result[0]["size_mb"] == 1.0
        assert result[0]["count"] == 10
        assert "codemie" in result[0]["location"]

    def test_extract_location_from_stat_no_traceback(self, service):
        """Test extracting location when no traceback."""
        mock_stat = Mock()
        mock_stat.traceback = None

        result = service._extract_location_from_stat(mock_stat)

        assert result == "unknown"

    def test_extract_location_from_stat_with_codemie(self, service):
        """Test extracting location with codemie frame."""
        mock_stat = Mock()
        mock_stat.traceback = Mock()
        mock_stat.traceback.format.return_value = [
            "  File \"/usr/lib/python3.12/threading.py\", line 100",
            "  File \"/app/codemie/service/test.py\", line 50\n    test_function()",
        ]

        result = service._extract_location_from_stat(mock_stat)

        assert "codemie" in result
        assert "test.py" in result

    def test_extract_location_from_stat_no_codemie(self, service):
        """Test extracting location without codemie frame."""
        mock_stat = Mock()
        mock_stat.traceback = Mock()
        mock_stat.traceback.format.return_value = ["  File \"/usr/lib/python3.12/threading.py\", line 100"]

        result = service._extract_location_from_stat(mock_stat)

        assert "threading.py" in result

    def test_extract_location_from_stat_empty_frames(self, service):
        """Test extracting location with empty frames."""
        mock_stat = Mock()
        mock_stat.traceback = Mock()
        mock_stat.traceback.format.return_value = []

        result = service._extract_location_from_stat(mock_stat)

        assert result == "unknown"

    def test_singleton_instance(self):
        """Test that memory_profiling_service is properly initialized."""
        assert memory_profiling_service is not None
        assert isinstance(memory_profiling_service, MemoryProfilingService)


class TestMemorySnapshotData:
    """Test suite for MemorySnapshotData model."""

    def test_create_minimal(self):
        """Test creating snapshot data with minimal fields."""
        snapshot = MemorySnapshotData(
            id="test-id",
            timestamp="2024-01-01T00:00:00",
            description="Test",
            current_memory_mb=10.0,
            peak_memory_mb=15.0,
            traced_memory_blocks=100,
            top_allocations=[],
        )

        assert snapshot.id == "test-id"
        assert snapshot.current_memory_mb == 10.0
        assert snapshot.process_rss_mb is None

    def test_create_with_psutil_metrics(self):
        """Test creating snapshot data with psutil metrics."""
        snapshot = MemorySnapshotData(
            id="test-id",
            timestamp="2024-01-01T00:00:00",
            description="Test",
            current_memory_mb=10.0,
            peak_memory_mb=15.0,
            traced_memory_blocks=100,
            top_allocations=[],
            process_rss_mb=50.0,
            process_vms_mb=100.0,
            native_untracked_mb=40.0,
        )

        assert snapshot.process_rss_mb == 50.0
        assert snapshot.process_vms_mb == 100.0
        assert snapshot.native_untracked_mb == 40.0

    def test_metadata_default(self):
        """Test default metadata."""
        snapshot = MemorySnapshotData(
            id="test-id",
            timestamp="2024-01-01T00:00:00",
            description="Test",
            current_memory_mb=10.0,
            peak_memory_mb=15.0,
            traced_memory_blocks=100,
            top_allocations=[],
        )

        assert snapshot.metadata == {}


class TestMemorySnapshotStats:
    """Test suite for MemorySnapshotStats model."""

    def test_create_minimal(self):
        """Test creating stats with minimal fields."""
        stats = MemorySnapshotStats(
            current_memory_mb=10.0,
            peak_memory_mb=15.0,
            traced_memory_blocks=100,
            tracking_enabled=True,
        )

        assert stats.current_memory_mb == 10.0
        assert stats.tracking_enabled is True
        assert stats.process_rss_mb is None

    def test_create_with_psutil_metrics(self):
        """Test creating stats with psutil metrics."""
        stats = MemorySnapshotStats(
            current_memory_mb=10.0,
            peak_memory_mb=15.0,
            traced_memory_blocks=100,
            tracking_enabled=True,
            process_rss_mb=50.0,
            python_heap_mb=10.0,
            native_untracked_mb=40.0,
        )

        assert stats.process_rss_mb == 50.0
        assert stats.python_heap_mb == 10.0
        assert stats.native_untracked_mb == 40.0
