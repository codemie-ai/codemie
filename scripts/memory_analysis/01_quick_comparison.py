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
Option 1: Quick Comparison & Analysis

Shows timeline of snapshots and compares oldest vs newest to detect long-term memory leaks.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from datetime import datetime

from memory_analysis_helpers import get_recent_snapshots, compare_snapshots


def main():
    print("=" * 100)
    print("🔍 OPTION 1: Quick Comparison & Analysis")
    print("=" * 100)
    print()

    # Get all snapshots sorted by time
    snapshots = get_recent_snapshots(limit=100)

    if not snapshots:
        print("❌ No snapshots found!")
        print("   Make sure MEMORY_PROFILING_ENABLED=True and the app has been running.")
        sys.exit(1)

    print(f"📁 Found {len(snapshots)} snapshots\n")

    # Show snapshot timeline
    print("📅 Snapshot Timeline (most recent first):")
    print("-" * 100)
    for i, snap in enumerate(snapshots[:15]):
        timestamp = datetime.fromisoformat(snap.timestamp)
        print(
            f"{i + 1:2d}. {timestamp.strftime('%Y-%m-%d %H:%M:%S')} | "
            f"Memory: {snap.current_memory_mb:8.2f} MB | "
            f"Peak: {snap.peak_memory_mb:8.2f} MB | "
            f"Blocks: {snap.traced_memory_blocks:,}"
        )
    print()

    # Compare oldest vs newest (to detect long-term leak)
    if len(snapshots) >= 2:
        print("🔍 Long-term Memory Growth Analysis:")
        print("=" * 100)

        diff = compare_snapshots(
            snapshots[-1].id,  # oldest
            snapshots[0].id,  # newest
        )

        print(f"📊 Time Period: {diff.time_elapsed_seconds / 3600:.1f} hours")
        print(f"📈 Memory Increase: {diff.memory_increase_mb:.2f} MB ({diff.memory_increase_percentage:.1f}%)")
        print(f"📦 Blocks Increase: {diff.blocks_increase:,}")
        print()
        print(f"💡 Analysis: {diff.analysis_summary}")
        print()

        print("🔥 Top 10 Memory Consumers:")
        print("-" * 100)
        for i, consumer in enumerate(diff.top_increases[:10], 1):
            print(f"{i:2d}. {consumer.size_mb:8.2f} MB | {consumer.count:7,} allocs | {consumer.location[:70]}")

        print()
        print("✅ Analysis complete!")
    else:
        print("⚠️  Need at least 2 snapshots for comparison analysis")


if __name__ == "__main__":
    main()
