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

"""
Memory profiling service for detecting memory leaks using tracemalloc + psutil.

⚠️ IMPORTANT UNDERSTANDING OF MEMORY TRACKING:

This service uses TWO complementary tools to give you the complete picture:

1. tracemalloc (Python heap only):
   - Tracks Python object allocations (lists, dicts, class instances, etc.)
   - DOES NOT track native C/C++ library memory
   - Shown in: current_memory_mb, peak_memory_mb, traced_memory_blocks

2. psutil (Complete process memory):
   - Tracks TOTAL process memory including native libraries
   - Includes: Python heap + C extensions + shared libraries + thread stacks
   - Shown in: process_rss_mb, process_vms_mb

WHY THE DIFFERENCE MATTERS:
   If process_rss_mb >> current_memory_mb:
   → Most memory is consumed by native libraries (LangChain, FastAPI, databases, ML models)
   → Use memray for deep analysis (see below)

   If process_rss_mb ≈ current_memory_mb:
   → Most memory is Python objects
   → Use tracemalloc analysis to investigate

MEMORY NOT TRACKED BY TRACEMALLOC:
   - Native C/C++ library allocations (numpy, pandas, ML libraries)
   - LangChain model weights loaded via native extensions
   - FastAPI/uvicorn internal buffers
   - PostgreSQL/Elasticsearch client caches
   - AWS/Azure/GCP SDK native memory
   - Memory-mapped files
   - Shared libraries
   - Thread stacks

Storage:
    Snapshots are stored using FileRepositoryFactory (FileSystem for local, AWS S3/Azure Blob/GCP for production)
    Format: Gzip-compressed JSON (snapshot_YYYY-MM-DD_HH-MM-SS_<uuid>.json.gz)
    Compression reduces storage by 70-90% typically

Export Formats:
    - JSON: Native format with detailed allocation info
    - Speedscope JSON: Compatible with https://speedscope.app (flamegraph visualization)
    - HTML: Self-contained report with charts
    - CSV: For Excel/data analysis

Usage:
    from codemie.service.monitoring.memory_profiling_service import memory_profiling_service

    # Start memory tracking (tracemalloc)
    memory_profiling_service.start_tracking()

    # Take a snapshot (captures both tracemalloc + psutil data)
    snapshot_id = memory_profiling_service.take_snapshot()

    # Get current stats without creating snapshot
    stats = memory_profiling_service.get_current_stats()
    print(f"Real memory: {stats.process_rss_mb:.2f} MB")
    print(f"Python heap: {stats.python_heap_mb:.2f} MB")
    print(f"Native libraries: {stats.native_untracked_mb:.2f} MB")

Visualization & Analysis Options:
    1. Built-in scripts (scripts/memory_analysis/):
       - Quick comparison, leak detection, HTML reports, CSV export
       - Run: python scripts/memory_analysis/09_complete_analysis.py

    2. Speedscope (recommended for flamegraphs):
       - Upload .speedscope.json to https://speedscope.app
       - Interactive flamegraph visualization

    3. memray (for deep native memory analysis):
       When process_rss_mb >> current_memory_mb (large native memory usage):
       - pip install memray
       - memray run -o memory.bin --native uvicorn codemie.rest_api.main:app
       - memray flamegraph memory.bin
       - memray table memory.bin
       This captures ALL allocations including native C/C++ libraries.
"""

import gzip
import json
import os
import tracemalloc
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

from codemie.configs.logger import logger
from codemie.configs.config import config
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.repository.base_file_repository import FileRepository


class MemorySnapshotData(BaseModel):
    """
    Data model for memory snapshot.

    This model captures both tracemalloc (Python heap) and psutil (total process) metrics
    to provide a complete picture of memory usage.
    """

    id: str
    timestamp: str
    description: str

    # Tracemalloc metrics (Python heap only) - ALWAYS PRESENT for backward compatibility
    current_memory_mb: float = Field(..., description="Python heap memory tracked by tracemalloc")
    peak_memory_mb: float = Field(..., description="Peak Python heap memory tracked by tracemalloc")
    traced_memory_blocks: int = Field(..., description="Number of memory blocks tracked by tracemalloc")
    top_allocations: List[Dict[str, Any]] = Field(..., description="Top memory allocations from tracemalloc")

    # psutil metrics (Complete process memory) - NEW, optional for backward compatibility
    process_rss_mb: Optional[float] = Field(None, description="Resident Set Size - actual memory used by process")
    process_vms_mb: Optional[float] = Field(None, description="Virtual Memory Size")
    process_shared_mb: Optional[float] = Field(None, description="Shared memory with other processes")
    process_percent: Optional[float] = Field(None, description="Percentage of system memory used")

    # System metrics - NEW, optional
    system_total_mb: Optional[float] = Field(None, description="Total system memory")
    system_available_mb: Optional[float] = Field(None, description="Available system memory")
    system_percent_used: Optional[float] = Field(None, description="System memory usage percentage")

    # Calculated metrics - NEW, optional
    native_untracked_mb: Optional[float] = Field(
        None, description="Memory not tracked by tracemalloc (RSS - tracemalloc) = native C/C++ libraries"
    )

    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemorySnapshotStats(BaseModel):
    """
    Current memory statistics without creating a snapshot.

    Lightweight stats for quick checks and API endpoints.
    """

    # Tracemalloc metrics (backward compatible)
    current_memory_mb: float = Field(..., description="Python heap memory from tracemalloc")
    peak_memory_mb: float = Field(..., description="Peak Python heap memory from tracemalloc")
    traced_memory_blocks: int = Field(..., description="Number of tracked memory blocks")
    tracking_enabled: bool = Field(..., description="Whether tracemalloc tracking is active")

    # psutil metrics (NEW, optional)
    process_rss_mb: Optional[float] = Field(None, description="Actual process memory (RSS)")
    process_vms_mb: Optional[float] = Field(None, description="Virtual memory size")
    python_heap_mb: Optional[float] = Field(None, description="Python heap (same as current_memory_mb)")
    native_untracked_mb: Optional[float] = Field(None, description="Native library memory")

    # System metrics (NEW, optional)
    system_available_mb: Optional[float] = Field(None, description="Available system memory")
    system_percent_used: Optional[float] = Field(None, description="System memory usage %")


class TopMemoryConsumer(BaseModel):
    """Top memory consuming location."""

    location: str
    size_mb: float
    count: int


class MemoryDiffResponse(BaseModel):
    """Memory snapshot comparison result."""

    snapshot1_id: str
    snapshot2_id: str
    snapshot1_timestamp: str
    snapshot2_timestamp: str
    time_elapsed_seconds: float
    memory_increase_mb: float
    memory_increase_percentage: float
    blocks_increase: int
    top_increases: List[TopMemoryConsumer]
    analysis_summary: str


class MemoryProfilingService:
    """
    Service for memory profiling and leak detection using tracemalloc.

    Features:
    - Manages tracemalloc lifecycle (start/stop)
    - Takes periodic memory snapshots
    - Stores snapshots using FileRepositoryFactory (local filesystem or cloud storage)
    - Compresses snapshots with gzip to reduce storage space
    - Exports to multiple formats (Speedscope, HTML, CSV)
    - Provides snapshot comparison for leak detection
    """

    def __init__(self):
        self._tracking_enabled = False
        self._snapshot_count = 0
        self._owner = config.CODEMIE_STORAGE_BUCKET_NAME
        self._file_repository: FileRepository = FileRepositoryFactory.get_current_repository()
        logger.info("Memory profiling service initialized", extra={"owner": self._owner})

    def _serialize_snapshot(self, snapshot_data: MemorySnapshotData) -> bytes:
        """Serialize and compress snapshot data to bytes using gzip"""
        json_str = json.dumps(snapshot_data.model_dump(), indent=None)
        return gzip.compress(json_str.encode('utf-8'))

    def start_tracking(self, nframe: int = 3) -> bool:
        """
        Start tracemalloc tracking.

        Args:
            nframe: Number of stack frames to capture (default: 3 for low CPU overhead)
                   Reduced from 10 to 3 to minimize CPU impact during allocation tracking.

        Returns:
            bool: True if tracking started successfully
        """
        if self._tracking_enabled:
            logger.warning("Memory tracking is already enabled")
            return False

        try:
            tracemalloc.start(nframe)
            self._tracking_enabled = True
            self._baseline_snapshot = tracemalloc.take_snapshot()
            logger.info(
                f"Memory tracking started with {nframe} stack frames",
                extra={"nframe": nframe, "operation": "start_memory_tracking"},
            )
            return True
        except Exception as e:
            # Ensure tracemalloc is stopped if an error occurs after starting
            try:
                if tracemalloc.is_tracing():
                    tracemalloc.stop()
            except Exception:
                pass  # Ignore errors during cleanup

            logger.error(
                f"Failed to start memory tracking: {e}",
                extra={"error": str(e), "operation": "start_memory_tracking"},
                exc_info=True,
            )
            return False

    def stop_tracking(self) -> bool:
        """
        Stop tracemalloc tracking.

        Returns:
            bool: True if tracking stopped successfully
        """
        if not self._tracking_enabled:
            logger.warning("Memory tracking is not enabled")
            return False

        try:
            tracemalloc.stop()
            self._tracking_enabled = False
            self._baseline_snapshot = None
            self._last_snapshot = None
            logger.info("Memory tracking stopped", extra={"operation": "stop_memory_tracking"})
            return True
        except Exception as e:
            logger.error(
                f"Failed to stop memory tracking: {e}",
                extra={"error": str(e), "operation": "stop_memory_tracking"},
                exc_info=True,
            )
            return False

    def is_tracking(self) -> bool:
        """Check if memory tracking is currently enabled."""
        return self._tracking_enabled

    def take_snapshot(
        self, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Take a memory snapshot and persist it using FileRepository..

        Captures both tracemalloc (Python heap) and psutil (total process) metrics
        to provide complete memory visibility. Compresses data with gzip before storage.

        Args:
            description: Optional description for the snapshot
            metadata: Optional metadata dictionary

        Returns:
            str: Snapshot ID if successful, None otherwise
        """
        if not self._tracking_enabled:
            logger.warning("Cannot take snapshot: memory tracking is not enabled")
            return None

        try:
            # Take tracemalloc snapshot (Python heap only)
            snapshot = tracemalloc.take_snapshot()
            self._last_snapshot = snapshot
            self._snapshot_count += 1

            # Get tracemalloc memory statistics (Python heap)
            current, peak = tracemalloc.get_traced_memory()
            current_mb = current / 1024 / 1024
            peak_mb = peak / 1024 / 1024

            # Calculate top memory consumers based on configured detail level
            detail_level = config.MEMORY_PROFILING_DETAIL_LEVEL.lower()
            if detail_level == "line":
                # Line-level detail: slower but more precise (shows exact line numbers)
                # Use when debugging specific memory leaks - apply filtering for better results
                filtered_snapshot = snapshot.filter_traces(
                    (
                        tracemalloc.Filter(False, "<frozen*>"),
                        tracemalloc.Filter(False, "*tracemalloc*"),
                        tracemalloc.Filter(False, "*importlib*"),
                    )
                )
                grouping_key = 'lineno'
                limit = 15  # Reduced from 30 to lower CPU impact
                top_stats = filtered_snapshot.statistics(grouping_key)[:limit]
            else:
                # File-level detail (default): 10-50x faster, groups allocations by file
                # Recommended for production - skip filtering to reduce CPU overhead
                grouping_key = 'filename'
                limit = 10  # Reduced from 20 to lower CPU impact
                top_stats = snapshot.statistics(grouping_key)[:limit]

            # Get psutil metrics (complete process memory) if available
            process_rss_mb = None
            process_vms_mb = None
            process_shared_mb = None
            process_percent = None
            system_total_mb = None
            system_available_mb = None
            system_percent_used = None
            native_untracked_mb = None

            if PSUTIL_AVAILABLE:
                try:
                    process = psutil.Process(os.getpid())
                    mem_info = process.memory_info()
                    sys_mem = psutil.virtual_memory()

                    # Process metrics
                    process_rss_mb = mem_info.rss / 1024 / 1024
                    process_vms_mb = mem_info.vms / 1024 / 1024
                    process_shared_mb = getattr(mem_info, 'shared', 0) / 1024 / 1024
                    process_percent = process.memory_percent()

                    # System metrics
                    system_total_mb = sys_mem.total / 1024 / 1024
                    system_available_mb = sys_mem.available / 1024 / 1024
                    system_percent_used = sys_mem.percent

                    # Calculate native (untracked) memory
                    native_untracked_mb = process_rss_mb - current_mb

                except Exception as e:
                    logger.warning(f"Failed to collect psutil metrics: {e}", extra={"error": str(e)})

            # Generate snapshot ID and filename with hostname/pod name
            snapshot_id = str(uuid.uuid4())
            timestamp = datetime.now()
            hostname = os.environ.get('HOSTNAME', 'unknown')  # Kubernetes sets HOSTNAME to pod name
            # Use .json.gz extension to indicate compressed JSON
            # Format: {prefix}/snapshot_YYYY-MM-DD_HH-MM-SS_hostname_uuid.json.gz
            filename = (
                f"{config.MEMORY_PROFILING_SNAPSHOT_PREFIX}/"
                f"snapshot_{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}_{hostname}_{snapshot_id[:8]}.json.gz"
            )

            # Prepare snapshot data with hostname in metadata
            snapshot_metadata = metadata or {}
            snapshot_metadata['hostname'] = hostname
            snapshot_metadata['pod_name'] = hostname  # In K8s, HOSTNAME is the pod name

            snapshot_data = MemorySnapshotData(
                id=snapshot_id,
                timestamp=timestamp.isoformat(),
                description=description or f"Automatic snapshot #{self._snapshot_count} from {hostname}",
                # Tracemalloc metrics (backward compatible - always present)
                current_memory_mb=current_mb,
                peak_memory_mb=peak_mb,
                traced_memory_blocks=len(snapshot.traces),
                top_allocations=self._format_top_allocations(top_stats),
                # psutil metrics (new - optional)
                process_rss_mb=process_rss_mb,
                process_vms_mb=process_vms_mb,
                process_shared_mb=process_shared_mb,
                process_percent=process_percent,
                system_total_mb=system_total_mb,
                system_available_mb=system_available_mb,
                system_percent_used=system_percent_used,
                native_untracked_mb=native_untracked_mb,
                metadata=snapshot_metadata,
            )

            # Serialize and compress snapshot data
            compressed_content = self._serialize_snapshot(snapshot_data)

            # Save using file repository (works with both filesystem and cloud storage)
            self._file_repository.write_file(
                name=filename,
                mime_type="application/gzip",  # Indicate it's gzipped content
                owner=self._owner,
                content=compressed_content,
            )

            # Prepare log message with complete metrics
            # Note: 'filename' is reserved in LogRecord, use 'snapshot_filename' instead
            log_extra = {
                "snapshot_id": snapshot_id,
                "snapshot_filename": filename,
                "owner": self._owner,
                "compressed_size_kb": len(compressed_content) / 1024,
                "python_heap_mb": current_mb,
                "peak_mb": peak_mb,
                "blocks": len(snapshot.traces),
                "operation": "take_memory_snapshot",
            }

            if process_rss_mb is not None:
                log_extra["process_rss_mb"] = process_rss_mb
                log_extra["native_untracked_mb"] = native_untracked_mb

            logger.info(
                f"Memory snapshot saved: {filename} "
                f"(Python: {current_mb:.2f} MB, Process RSS: {process_rss_mb:.2f} MB, "
                f"Compressed: {len(compressed_content) / 1024:.1f} KB)"
                if process_rss_mb
                else f"Memory snapshot saved: {filename} (Python: {current_mb:.2f} MB, "
                f"Compressed: {len(compressed_content) / 1024:.1f} KB)",
                extra=log_extra,
            )

            # Warn if native memory is very high
            if native_untracked_mb and native_untracked_mb > 100:
                logger.warning(
                    f"High native (untracked) memory detected: {native_untracked_mb:.2f} MB. "
                    f"Consider using memray for deep analysis.",
                    extra={"native_untracked_mb": native_untracked_mb},
                )

            return snapshot_id

        except Exception as e:
            logger.error(
                f"Failed to take memory snapshot: {e}",
                extra={"error": str(e), "operation": "take_memory_snapshot"},
                exc_info=True,
            )
            return None

    def get_current_stats(self) -> Optional[MemorySnapshotStats]:
        """
        Get current memory statistics without creating a snapshot.

        Includes both tracemalloc (Python heap) and psutil (complete process) metrics.

        Returns:
            MemorySnapshotStats with current memory information
        """
        if not self._tracking_enabled:
            return MemorySnapshotStats(
                current_memory_mb=0.0,
                peak_memory_mb=0.0,
                traced_memory_blocks=0,
                tracking_enabled=False,
            )

        try:
            # Get tracemalloc stats
            current, peak = tracemalloc.get_traced_memory()
            snapshot = tracemalloc.take_snapshot()
            current_mb = current / 1024 / 1024
            peak_mb = peak / 1024 / 1024

            # Get psutil stats if available
            process_rss_mb = None
            process_vms_mb = None
            python_heap_mb = current_mb
            native_untracked_mb = None
            system_available_mb = None
            system_percent_used = None

            if PSUTIL_AVAILABLE:
                try:
                    process = psutil.Process(os.getpid())
                    mem_info = process.memory_info()
                    sys_mem = psutil.virtual_memory()

                    process_rss_mb = mem_info.rss / 1024 / 1024
                    process_vms_mb = mem_info.vms / 1024 / 1024
                    system_available_mb = sys_mem.available / 1024 / 1024
                    system_percent_used = sys_mem.percent
                    native_untracked_mb = process_rss_mb - current_mb
                except Exception as e:
                    logger.warning(f"Failed to collect psutil metrics: {e}", extra={"error": str(e)})

            return MemorySnapshotStats(
                current_memory_mb=current_mb,
                peak_memory_mb=peak_mb,
                traced_memory_blocks=len(snapshot.traces),
                tracking_enabled=True,
                process_rss_mb=process_rss_mb,
                process_vms_mb=process_vms_mb,
                python_heap_mb=python_heap_mb,
                native_untracked_mb=native_untracked_mb,
                system_available_mb=system_available_mb,
                system_percent_used=system_percent_used,
            )
        except Exception as e:
            logger.error(f"Failed to get current memory stats: {e}", extra={"error": str(e)}, exc_info=True)
            return None

    def _format_top_allocations(self, stats: List) -> List[Dict[str, Any]]:
        """Format tracemalloc statistics for storage."""
        formatted = []
        for stat in stats:
            location = self._extract_location_from_stat(stat)
            formatted.append(
                {
                    "location": location,
                    "size_mb": stat.size / 1024 / 1024,
                    "count": stat.count,
                }
            )
        return formatted

    def _extract_location_from_stat(self, stat) -> str:
        """Extract the most relevant location from a tracemalloc statistic."""
        if not stat.traceback:
            return "unknown"

        frames = stat.traceback.format()
        # Find first frame that's not from standard library
        for frame in frames:
            if '/codemie/' in frame or '/src/' in frame:
                return frame.strip()

        # Use first frame if no codemie frame found
        return frames[0].strip() if frames else "unknown"


# Singleton instance
memory_profiling_service = MemoryProfilingService()
