#!/usr/bin/env python3
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
Memory Analysis Helper Functions

This module contains helper functions for analyzing memory profiling snapshots.
These functions are intended for offline analysis scripts, not production code.

The functions in this module:
- Read snapshot files from local directories (data/memory_snapshots/ or codemie-storage/)
- Compare snapshots to detect memory leaks
- Export snapshots to various formats (HTML, Speedscope, CSV)
- Generate analysis reports and visualizations
- Get detailed memory breakdowns for analysis

These are NOT part of the production MemoryProfilingService because they:
- Work with LOCAL files only (not cloud storage)
- Are for offline analysis and debugging
- Have different dependencies and performance characteristics
"""

import gzip
import html
import json
import os
import tracemalloc
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

from codemie.configs.logger import logger
from codemie.service.monitoring.memory_profiling_service import (
    MemorySnapshotData,
    TopMemoryConsumer,
    MemoryDiffResponse,
)


def get_recent_snapshots(limit: int = 20, hours_back: Optional[int] = None) -> List[MemorySnapshotData]:
    """
    Get recent memory snapshots from local directory.

    ⚠️ This function works with LOCAL files only (not cloud storage).
    For local development, files are in codemie-storage/monitoring/memory_snapshots/
    For analysis, copy/symlink files to data/memory_snapshots/

    Args:
        limit: Maximum number of snapshots to return
        hours_back: Optional filter for snapshots within last N hours

    Returns:
        List of MemorySnapshotData sorted by timestamp (newest first)
    """
    try:
        # Check both locations with strict path validation
        local_dirs = [
            Path("data/memory_snapshots"),
            Path("codemie-storage/monitoring/memory_snapshots"),
        ]

        snapshot_files = []
        for local_dir in local_dirs:
            # Resolve to absolute path and validate it's within expected directories
            try:
                resolved_dir = local_dir.resolve(strict=False)
                # Ensure path doesn't contain traversal patterns and is within workspace
                if ".." in str(resolved_dir) or not resolved_dir.is_relative_to(Path.cwd().resolve()):
                    logger.warning(
                        f"Skipping potentially unsafe directory path: {local_dir}",
                        extra={"path": str(local_dir), "operation": "get_recent_snapshots"},
                    )
                    continue
            except Exception as e:
                logger.warning(f"Failed to resolve directory path {local_dir}: {e}")
                continue

            if resolved_dir.exists() and resolved_dir.is_dir():
                # Validate each file before adding to list
                for file_path in resolved_dir.glob("snapshot_*.json.gz"):
                    # Additional path traversal protection
                    if file_path.is_file() and file_path.is_relative_to(resolved_dir):
                        snapshot_files.append(file_path)

        if not snapshot_files:
            logger.warning(
                "⚠️ No snapshot files found in data/memory_snapshots/ or "
                "codemie-storage/monitoring/memory_snapshots/\n"
                "   To analyze snapshots:\n"
                "   1. For local: Files should be in codemie-storage/monitoring/memory_snapshots/\n"
                "   2. For analysis: Copy or symlink to data/memory_snapshots/\n"
                "   3. For production: Download .json.gz files from cloud storage first"
            )
            return []

        snapshots = []
        cutoff_time = None
        if hours_back:
            cutoff_time = datetime.now() - timedelta(hours=hours_back)

        # Sort by modification time (newest first)
        snapshot_files = sorted(snapshot_files, key=lambda p: p.stat().st_mtime, reverse=True)

        # Optimize: Apply time filter based on file modification time before loading
        if cutoff_time:
            cutoff_timestamp = cutoff_time.timestamp()
            snapshot_files = [f for f in snapshot_files if f.stat().st_mtime >= cutoff_timestamp]

        for file_path in snapshot_files:
            try:
                # Read and decompress local file
                with gzip.open(file_path, 'rt', encoding='utf-8') as f:
                    data = json.load(f)

                    # Double-check timestamp filter (in case file mtime != snapshot timestamp)
                    if cutoff_time:
                        snapshot_time = datetime.fromisoformat(data['timestamp'])
                        if snapshot_time < cutoff_time:
                            continue

                    snapshots.append(MemorySnapshotData(**data))

                    if len(snapshots) >= limit:
                        break
            except Exception as e:
                logger.warning(f"Failed to load snapshot from {file_path}: {e}")
                continue

        return snapshots

    except Exception as e:
        logger.error(
            f"Failed to retrieve recent snapshots: {e}",
            extra={"limit": limit, "hours_back": hours_back, "error": str(e)},
            exc_info=True,
        )
        return []


def compare_snapshots(snapshot1_id: str, snapshot2_id: str, top_n: int = 20) -> Optional[MemoryDiffResponse]:
    """
    Compare two memory snapshots to identify memory growth.

    ⚠️ This function works with LOCAL files only (for analysis scripts).

    Args:
        snapshot1_id: ID of the first (older) snapshot
        snapshot2_id: ID of the second (newer) snapshot
        top_n: Number of top differences to return

    Returns:
        MemoryDiffResponse with comparison results
    """
    try:
        # Check both locations
        local_dirs = [
            Path("data/memory_snapshots"),
            Path("codemie-storage/monitoring/memory_snapshots"),
        ]

        snapshot1 = None
        snapshot2 = None

        for local_dir in local_dirs:
            if not local_dir.exists():
                continue

            for file_path in local_dir.glob("snapshot_*.json.gz"):
                try:
                    with gzip.open(file_path, 'rt', encoding='utf-8') as f:
                        data = json.load(f)
                        if data.get('id') == snapshot1_id:
                            snapshot1 = MemorySnapshotData(**data)
                        if data.get('id') == snapshot2_id:
                            snapshot2 = MemorySnapshotData(**data)

                        if snapshot1 and snapshot2:
                            break
                except Exception:
                    continue

            if snapshot1 and snapshot2:
                break

        if not snapshot1 or not snapshot2:
            logger.warning(
                "Cannot compare snapshots: one or both not found in local directory",
                extra={"snapshot1_id": snapshot1_id, "snapshot2_id": snapshot2_id},
            )
            return None

        # Parse timestamps
        time1 = datetime.fromisoformat(snapshot1.timestamp)
        time2 = datetime.fromisoformat(snapshot2.timestamp)

        # Calculate differences
        memory_increase_mb = snapshot2.current_memory_mb - snapshot1.current_memory_mb
        memory_increase_pct = (
            (memory_increase_mb / snapshot1.current_memory_mb * 100) if snapshot1.current_memory_mb > 0 else 0.0
        )

        blocks_increase = snapshot2.traced_memory_blocks - snapshot1.traced_memory_blocks

        # Identify top memory growth areas from snapshot2
        top_increases = [
            TopMemoryConsumer(
                location=alloc.get("location", "unknown"),
                size_mb=alloc.get("size_mb", 0.0),
                count=alloc.get("count", 0),
            )
            for alloc in snapshot2.top_allocations[:top_n]
        ]

        diff_response = MemoryDiffResponse(
            snapshot1_id=snapshot1_id,
            snapshot2_id=snapshot2_id,
            snapshot1_timestamp=snapshot1.timestamp,
            snapshot2_timestamp=snapshot2.timestamp,
            time_elapsed_seconds=(time2 - time1).total_seconds(),
            memory_increase_mb=memory_increase_mb,
            memory_increase_percentage=memory_increase_pct,
            blocks_increase=blocks_increase,
            top_increases=top_increases,
            analysis_summary=_generate_analysis_summary(memory_increase_mb, memory_increase_pct, blocks_increase),
        )

        logger.info(
            f"Snapshot comparison completed: {memory_increase_mb:.2f} MB increase",
            extra={
                "snapshot1_id": snapshot1_id,
                "snapshot2_id": snapshot2_id,
                "memory_increase_mb": memory_increase_mb,
                "operation": "compare_snapshots",
            },
        )

        return diff_response

    except Exception as e:
        logger.error(
            f"Failed to compare snapshots: {e}",
            extra={"snapshot1_id": snapshot1_id, "snapshot2_id": snapshot2_id, "error": str(e)},
            exc_info=True,
        )
        return None


def export_to_html(snapshot_id: str, output_file: Optional[str] = None) -> Optional[str]:
    """
    Export snapshot to HTML report with interactive visualizations.

    ⚠️ This function works with LOCAL files only (for analysis scripts).

    Args:
        snapshot_id: Snapshot ID to export
        output_file: Output file path (optional)

    Returns:
        str: Path to exported HTML file, or None on error
    """
    try:
        # Find snapshot in local directories
        local_dirs = [
            Path("data/memory_snapshots"),
            Path("codemie-storage/monitoring/memory_snapshots"),
        ]

        snapshot = None
        for local_dir in local_dirs:
            if not local_dir.exists():
                continue

            for file_path in local_dir.glob("snapshot_*.json.gz"):
                try:
                    with gzip.open(file_path, 'rt', encoding='utf-8') as f:
                        data = json.load(f)
                        if data.get('id') == snapshot_id:
                            snapshot = MemorySnapshotData(**data)
                            break
                except Exception:
                    continue

            if snapshot:
                break

        if not snapshot:
            logger.error(f"Snapshot {snapshot_id} not found in local directories")
            return None

        # Generate output filename if not provided
        if not output_file:
            output_dir = Path("data/memory_snapshots")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = str(output_dir / f"report_{snapshot_id[:8]}.html")

        # Generate HTML report
        html_content = _generate_html_report(snapshot)

        # Save to file
        with open(output_file, 'w') as f:
            f.write(html_content)

        logger.info(
            f"Exported snapshot to HTML report: {output_file}",
            extra={"snapshot_id": snapshot_id, "output_file": output_file},
        )

        return output_file

    except Exception as e:
        logger.error(
            f"Failed to export to HTML: {e}", extra={"snapshot_id": snapshot_id, "error": str(e)}, exc_info=True
        )
        return None


def export_to_speedscope(snapshot_id: str, output_file: Optional[str] = None) -> Optional[str]:
    """
    Export snapshot to Speedscope JSON format for interactive flamegraph visualization.

    Upload the generated file to https://speedscope.app for visualization.

    ⚠️ This function works with LOCAL files only (for analysis scripts).

    Args:
        snapshot_id: Snapshot ID to export
        output_file: Output file path (optional)

    Returns:
        str: Path to exported Speedscope JSON file, or None on error
    """
    try:
        # Find snapshot in local directories
        local_dirs = [
            Path("data/memory_snapshots"),
            Path("codemie-storage/monitoring/memory_snapshots"),
        ]

        snapshot = None
        for local_dir in local_dirs:
            if not local_dir.exists():
                continue

            for file_path in local_dir.glob("snapshot_*.json.gz"):
                try:
                    with gzip.open(file_path, 'rt', encoding='utf-8') as f:
                        data = json.load(f)
                        if data.get('id') == snapshot_id:
                            snapshot = MemorySnapshotData(**data)
                            break
                except Exception:
                    continue

            if snapshot:
                break

        if not snapshot:
            logger.error(f"Snapshot {snapshot_id} not found in local directories")
            return None

        # Generate output filename if not provided
        if not output_file:
            output_dir = Path("data/memory_snapshots")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = str(output_dir / f"speedscope_{snapshot_id[:8]}.speedscope.json")

        # Convert to Speedscope format
        speedscope_data = _convert_to_speedscope_format(snapshot)

        # Save to file
        with open(output_file, 'w') as f:
            json.dump(speedscope_data, f, indent=2)

        logger.info(
            f"Exported snapshot to Speedscope format: {output_file}",
            extra={"snapshot_id": snapshot_id, "output_file": output_file},
        )

        return output_file

    except Exception as e:
        logger.error(
            f"Failed to export to Speedscope: {e}",
            extra={"snapshot_id": snapshot_id, "error": str(e)},
            exc_info=True,
        )
        return None


def _generate_analysis_summary(memory_increase_mb: float, memory_increase_pct: float, blocks_increase: int) -> str:
    """Generate a human-readable analysis summary."""
    if memory_increase_mb < 0:
        return "✅ Memory usage decreased - no leak detected."
    elif memory_increase_mb < 1.0:
        return "✅ Minimal memory increase (<1 MB) - normal operation."
    elif memory_increase_mb < 10.0:
        return (
            f"⚠️ Moderate memory increase of {memory_increase_mb:.2f} MB ({memory_increase_pct:.1f}%) - "
            f"monitor for trends over time."
        )
    elif memory_increase_mb < 50.0:
        return (
            f"⚠️ Significant memory increase of {memory_increase_mb:.2f} MB ({memory_increase_pct:.1f}%) - "
            f"investigate top allocations. May indicate a memory leak."
        )
    else:
        return (
            f"🚨 CRITICAL memory increase of {memory_increase_mb:.2f} MB ({memory_increase_pct:.1f}%) - "
            f"LIKELY MEMORY LEAK DETECTED! Immediate investigation required."
        )


def _generate_html_report(snapshot: MemorySnapshotData) -> str:
    """Generate HTML report with charts for a snapshot."""
    # Escape all user-provided data to prevent XSS
    snapshot_id_escaped = html.escape(snapshot.id[:8])
    timestamp_escaped = html.escape(snapshot.timestamp)
    description_escaped = html.escape(snapshot.description)

    # Generate analysis_hint if not present in snapshot (for backward compatibility)
    if hasattr(snapshot, 'analysis_hint') and snapshot.analysis_hint:
        analysis_hint = snapshot.analysis_hint
    elif snapshot.process_rss_mb is not None and snapshot.current_memory_mb is not None:
        # Generate hint on-the-fly using existing data
        analysis_hint = _generate_memory_analysis_hint(snapshot.process_rss_mb, snapshot.current_memory_mb)
    else:
        analysis_hint = ""

    analysis_hint_escaped = html.escape(analysis_hint)

    # Prepare data for chart with escaped values
    allocations_json = json.dumps(
        [
            {
                "location": html.escape(a.get("location", "unknown")[:50]),
                "size_mb": a.get("size_mb", 0.0),
                "count": a.get("count", 0),
            }
            for a in snapshot.top_allocations[:20]
        ]
    )

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Memory Snapshot Report - {snapshot_id_escaped}</title>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #333;
            border-bottom: 3px solid #4CAF50;
            padding-bottom: 10px;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }}
        .stat-card {{
            background: #f9f9f9;
            padding: 20px;
            border-radius: 6px;
            border-left: 4px solid #4CAF50;
        }}
        .stat-card h3 {{
            margin: 0 0 10px 0;
            color: #666;
            font-size: 14px;
        }}
        .stat-card .value {{
            font-size: 24px;
            font-weight: bold;
            color: #333;
        }}
        .allocation-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }}
        .allocation-table th {{
            background: #4CAF50;
            color: white;
            padding: 12px;
            text-align: left;
        }}
        .allocation-table td {{
            padding: 10px;
            border-bottom: 1px solid #ddd;
        }}
        .allocation-table tr:hover {{
            background: #f5f5f5;
        }}
        .location {{
            font-family: monospace;
            font-size: 12px;
            word-break: break-all;
        }}
        .chart-container {{
            margin: 30px 0;
            height: 400px;
        }}
        .info {{
            background: #e3f2fd;
            padding: 15px;
            border-radius: 6px;
            margin: 20px 0;
        }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
    <div class="container">
        <h1>🔍 Memory Snapshot Report</h1>

        <div class="info">
            <strong>Snapshot ID:</strong> {snapshot_id_escaped}<br>
            <strong>Timestamp:</strong> {timestamp_escaped}<br>
            <strong>Description:</strong> {description_escaped}
        </div>

        <div class="stats">
            <div class="stat-card">
                <h3>Python Heap Memory</h3>
                <div class="value">{snapshot.current_memory_mb:.2f} MB</div>
                <small>Tracked by tracemalloc</small>
            </div>
            <div class="stat-card">
                <h3>Peak Python Memory</h3>
                <div class="value">{snapshot.peak_memory_mb:.2f} MB</div>
                <small>Peak heap usage</small>
            </div>"""

    # Add process RSS if available
    if snapshot.process_rss_mb is not None:
        html_content += f"""
            <div class="stat-card" style="border-left-color: #2196F3;">
                <h3>Total Process Memory (RSS)</h3>
                <div class="value">{snapshot.process_rss_mb:.2f} MB</div>
                <small>Includes native libraries</small>
            </div>"""

    # Add native untracked if available
    if snapshot.native_untracked_mb is not None:
        html_content += f"""
            <div class="stat-card" style="border-left-color: #FF9800;">
                <h3>Native (Untracked) Memory</h3>
                <div class="value">{snapshot.native_untracked_mb:.2f} MB</div>
                <small>C/C++ libraries, ML models</small>
            </div>"""

    html_content += f"""
            <div class="stat-card">
                <h3>Memory Blocks</h3>
                <div class="value">{snapshot.traced_memory_blocks:,}</div>
                <small>Python allocations tracked</small>
            </div>
            <div class="stat-card">
                <h3>Top Allocations</h3>
                <div class="value">{len(snapshot.top_allocations)}</div>
                <small>Detailed breakdown available</small>
            </div>
        </div>"""

    # Add analysis hint if available (escaped)
    if analysis_hint:
        html_content += f"""
        <div class="info" style="background: #fff3cd; border-left: 4px solid #ff9800;">
            <strong>💡 Memory Analysis:</strong> {analysis_hint_escaped}
        </div>"""

    # Add system info if available
    if snapshot.system_available_mb is not None:
        html_content += f"""
        <div class="info" style="background: #e8f5e9;">
            <strong>🖥️ System Memory:</strong>
            Available: {snapshot.system_available_mb:.2f} MB
            {f" | Total: {snapshot.system_total_mb:.2f} MB" if snapshot.system_total_mb else ""}
            {f" | Usage: {snapshot.system_percent_used:.1f}%" if snapshot.system_percent_used else ""}
        </div>"""

    html_content += """

        <h2>📊 Top Memory Allocations</h2>
        <div class="chart-container">
            <canvas id="memoryChart"></canvas>
        </div>

        <h2>📋 Allocation Details</h2>
        <table class="allocation-table">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Location</th>
                    <th>Size (MB)</th>
                    <th>Count</th>
                </tr>
            </thead>
            <tbody>
"""

    # Escape allocation data for HTML table
    for idx, alloc in enumerate(snapshot.top_allocations[:30], 1):
        location_escaped = html.escape(alloc.get('location', 'unknown'))
        size_mb = alloc.get('size_mb', 0.0)
        count = alloc.get('count', 0)
        html_content += f"""
                <tr>
                    <td>{idx}</td>
                    <td class="location">{location_escaped}</td>
                    <td>{size_mb:.4f}</td>
                    <td>{count:,}</td>
                </tr>
"""

    html_content += f"""
            </tbody>
        </table>

        <div class="info">
            <strong>💡 Tip:</strong> For deeper analysis, export this snapshot to Speedscope format
            and upload to <a href="https://speedscope.app" target="_blank">https://speedscope.app</a>
            for interactive flamegraph visualization.
        </div>
    </div>

    <script>
        const data = {allocations_json};
        const ctx = document.getElementById('memoryChart').getContext('2d');
        new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: data.map((d, i) => `#${{i+1}}`),
                datasets: [{{
                    label: 'Memory Usage (MB)',
                    data: data.map(d => d.size_mb),
                    backgroundColor: 'rgba(76, 175, 80, 0.6)',
                    borderColor: 'rgba(76, 175, 80, 1)',
                    borderWidth: 1
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    title: {{
                        display: true,
                        text: 'Top 20 Memory Allocations'
                    }},
                    tooltip: {{
                        callbacks: {{
                            afterLabel: function(context) {{
                                const item = data[context.dataIndex];
                                return `Count: ${{item.count}}\\nLocation: ${{item.location}}`;
                            }}
                        }}
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        title: {{
                            display: true,
                            text: 'Memory (MB)'
                        }}
                    }},
                    x: {{
                        title: {{
                            display: true,
                            text: 'Allocation Rank'
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""
    return html_content


def _convert_to_speedscope_format(snapshot: MemorySnapshotData) -> Dict[str, Any]:
    """
    Convert memory snapshot to Speedscope JSON format.

    Speedscope format spec: https://www.speedscope.app/file-format-schema.json
    """
    # Build profiles array with a single profile
    profiles = []

    # Create a simple time-ordered profile showing memory allocations
    # Each frame represents a memory allocation location
    frames = []
    samples = []
    weights = []

    frame_map = {}  # location -> frame index

    for alloc in snapshot.top_allocations:
        location = alloc.get("location", "unknown")
        size_mb = alloc.get("size_mb", 0.0)

        # Get or create frame index for this location
        if location not in frame_map:
            frame_map[location] = len(frames)
            frames.append({"name": location})

        frame_idx = frame_map[location]

        # Add sample (stack trace with single frame)
        samples.append([frame_idx])

        # Add weight (memory size in bytes, Speedscope expects integer weights)
        weights.append(int(size_mb * 1024 * 1024))

    profile = {
        "type": "sampled",
        "name": f"Memory Snapshot {snapshot.id[:8]}",
        "unit": "bytes",
        "startValue": 0,
        "endValue": sum(weights),
        "samples": samples,
        "weights": weights,
    }

    profiles.append(profile)

    # Build Speedscope JSON structure
    speedscope_data = {
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "shared": {"frames": frames},
        "profiles": profiles,
        "name": f"Memory Snapshot {snapshot.timestamp}",
        "activeProfileIndex": 0,
        "exporter": f"CodeMie Memory Profiler v0.8.0 (snapshot: {snapshot.id[:8]})",
    }

    return speedscope_data


def get_memory_breakdown(tracking_enabled: bool = False) -> Dict[str, Any]:
    """
    Get detailed memory breakdown by category.

    ⚠️ This function is for ANALYSIS SCRIPTS, not production monitoring.
    For production, use MemoryProfilingService.get_current_stats() instead.

    Args:
        tracking_enabled: Whether tracemalloc tracking is enabled

    Returns:
        dict with memory usage broken down into:
        - total_rss_mb: Total process memory (RSS)
        - python_heap_mb: Python object memory (tracemalloc)
        - native_untracked_mb: Native library memory (RSS - Python heap)
        - virtual_mb: Virtual memory size
        - shared_mb: Shared memory
        - system_available_mb: Available system memory
        - analysis_hint: Human-readable hint about what to investigate
    """
    breakdown = {}

    # Get tracemalloc data if tracking is enabled
    if tracking_enabled:
        try:
            current, peak = tracemalloc.get_traced_memory()
            breakdown["python_heap_mb"] = current / 1024 / 1024
            breakdown["python_peak_mb"] = peak / 1024 / 1024
        except Exception as e:
            logger.warning(f"Failed to get tracemalloc data: {e}")
            breakdown["python_heap_mb"] = 0.0
            breakdown["python_peak_mb"] = 0.0
    else:
        breakdown["python_heap_mb"] = 0.0
        breakdown["python_peak_mb"] = 0.0
        breakdown["warning"] = "tracemalloc tracking not enabled"

    # Get psutil data if available
    if PSUTIL_AVAILABLE:
        try:
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            sys_mem = psutil.virtual_memory()

            breakdown["total_rss_mb"] = mem_info.rss / 1024 / 1024
            breakdown["virtual_mb"] = mem_info.vms / 1024 / 1024
            breakdown["shared_mb"] = getattr(mem_info, 'shared', 0) / 1024 / 1024
            breakdown["native_untracked_mb"] = breakdown["total_rss_mb"] - breakdown["python_heap_mb"]
            breakdown["system_total_mb"] = sys_mem.total / 1024 / 1024
            breakdown["system_available_mb"] = sys_mem.available / 1024 / 1024
            breakdown["system_percent_used"] = sys_mem.percent

            # Calculate percentages
            if breakdown["total_rss_mb"] > 0:
                breakdown["python_percent"] = breakdown["python_heap_mb"] / breakdown["total_rss_mb"] * 100
                breakdown["native_percent"] = breakdown["native_untracked_mb"] / breakdown["total_rss_mb"] * 100

            # Generate analysis hint
            breakdown["analysis_hint"] = _generate_memory_analysis_hint(
                breakdown["total_rss_mb"], breakdown["python_heap_mb"]
            )

        except Exception as e:
            logger.warning(f"Failed to collect psutil metrics: {e}")
            breakdown["error"] = str(e)
    else:
        breakdown["warning"] = "psutil not available - install with: pip install psutil"

    return breakdown


def _generate_memory_analysis_hint(rss_mb: float, python_mb: float) -> str:
    """
    Generate analysis hint based on memory breakdown.

    Helps users understand where memory is being consumed and what tools to use.
    """
    if rss_mb <= 0 or python_mb < 0:
        return "⚠️ Unable to generate analysis hint (invalid memory values)"

    native_mb = rss_mb - python_mb
    native_percent = (native_mb / rss_mb * 100) if rss_mb > 0 else 0

    if native_percent > 90:
        return (
            f"🔴 {native_percent:.1f}% of memory ({native_mb:.2f} MB) is in NATIVE CODE (not Python). "
            f"This is typical for ML models, database drivers, or web servers. "
            f"Use memray for deep analysis: memray run --native uvicorn ..."
        )
    elif native_percent > 70:
        return (
            f"🟠 {native_percent:.1f}% of memory ({native_mb:.2f} MB) is in native code. "
            f"Likely LangChain models, FastAPI internals, or cloud SDKs. "
            f"Use memray if investigating native allocations."
        )
    elif native_percent > 40:
        return (
            f"🟡 {native_percent:.1f}% native ({native_mb:.2f} MB), "
            f"{100 - native_percent:.1f}% Python ({python_mb:.2f} MB). "
            f"Mixed allocation. Check both tracemalloc reports and consider memray."
        )
    else:
        return (
            f"🟢 {100 - native_percent:.1f}% of memory ({python_mb:.2f} MB) is Python objects. "
            f"Use tracemalloc analysis tools in scripts/memory_analysis/ to investigate."
        )
