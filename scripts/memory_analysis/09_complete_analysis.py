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
Option 9: Complete Memory Analysis

Runs a comprehensive analysis combining multiple checks.
This is the "all-in-one" option that gives you a complete picture.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from datetime import datetime

from memory_analysis_helpers import get_recent_snapshots, compare_snapshots


def main():
    print("=" * 100)
    print("🔍 OPTION 9: Complete Memory Analysis")
    print("=" * 100)
    print()

    # Get snapshots
    snapshots = get_recent_snapshots(limit=50)

    if not snapshots:
        print("❌ No snapshots found. Enable memory profiling first.")
        sys.exit(1)

    print(f"📁 Found {len(snapshots)} snapshots")
    print()

    # === SECTION 1: Current Status ===
    print("📊 SECTION 1: Current Memory Status")
    print("-" * 100)
    current = snapshots[0]
    print(f"⏰ Latest Snapshot: {current.timestamp}")
    print(f"💾 Current Memory:  {current.current_memory_mb:.2f} MB")
    print(f"📈 Peak Memory:     {current.peak_memory_mb:.2f} MB")
    print(f"📦 Traced Blocks:   {current.traced_memory_blocks:,}")
    print()

    # === SECTION 2: Growth Analysis ===
    diff = None  # Initialize diff to None to avoid undefined variable access
    if len(snapshots) >= 2:
        print("📈 SECTION 2: Memory Growth Analysis")
        print("-" * 100)

        diff = compare_snapshots(snapshots[-1].id, snapshots[0].id)

        hours = diff.time_elapsed_seconds / 3600
        print(f"⏱️  Time Period:      {hours:.1f} hours ({len(snapshots)} snapshots)")
        print(f"📊 Memory Change:    {diff.memory_increase_mb:+.2f} MB ({diff.memory_increase_percentage:+.1f}%)")
        print(f"📦 Blocks Change:    {diff.blocks_increase:+,}")
        print()
        print(f"💡 {diff.analysis_summary}")
        print()

        if diff.memory_increase_mb > 10:
            print("🚨 ALERT: Significant memory growth detected!")
            print()

    # === SECTION 3: Top Memory Consumers ===
    print("🔥 SECTION 3: Top 10 Memory Consumers (Current)")
    print("-" * 100)
    for i, alloc in enumerate(current.top_allocations[:10], 1):
        size_mb = alloc.get('size_mb', 0.0)
        count = alloc.get('count', 0)
        location = alloc.get('location', 'unknown')[:60]
        print(f"{i:2d}. {size_mb:7.2f} MB | {count:7,} allocs | {location}")
    print()

    # === SECTION 4: Recent Trend ===
    if len(snapshots) >= 5:
        print("📉 SECTION 4: Recent Memory Trend (Last 5 Snapshots)")
        print("-" * 100)
        for _i, snap in enumerate(snapshots[:5]):
            timestamp = datetime.fromisoformat(snap.timestamp)
            print(
                f"{timestamp.strftime('%m/%d %H:%M')} | "
                f"Memory: {snap.current_memory_mb:7.2f} MB | "
                f"Blocks: {snap.traced_memory_blocks:8,}"
            )

        # Calculate trend
        recent_mems = [s.current_memory_mb for s in snapshots[:5]]
        trend = recent_mems[0] - recent_mems[-1]
        if abs(trend) > 1:
            direction = "📈 Increasing" if trend > 0 else "📉 Decreasing"
            print(f"\n💡 Trend: {direction} ({trend:+.2f} MB over last 5 snapshots)")
        else:
            print("\n💡 Trend: ✅ Stable")
        print()

    # === SECTION 5: Recommendations ===
    print("💡 SECTION 5: Recommendations")
    print("-" * 100)

    if len(snapshots) < 10:
        print("⚠️  Limited data: Let the application run longer to collect more snapshots")
    elif diff and diff.memory_increase_mb > 50:
        print("🚨 CRITICAL: Immediate investigation required!")
        print("   • Run Option 8 to find leak candidates")
        print("   • Use memray for deep analysis: memray run -o trace.bin uvicorn ...")
        print("   • Check for unclosed resources or growing collections")
    elif diff and diff.memory_increase_mb > 10:
        print("⚠️  WARNING: Moderate memory growth detected")
        print("   • Monitor trend over next few hours")
        print("   • Run Option 8 to identify growing locations")
        print("   • Review recent code changes")
    else:
        print("✅ Memory usage appears normal")
        print("   • Continue periodic monitoring")
        print("   • Review snapshots weekly")

    print()

    # === SECTION 6: Export Options ===
    print("📤 SECTION 6: Available Exports")
    print("-" * 100)
    print("Generate detailed reports with:")
    print("   • python 03_export_html.py       - Interactive HTML report with charts")
    print("   • python 04_export_speedscope.py - Flamegraph for https://speedscope.app")
    print("   • python 07_export_csv.py        - CSV for Excel/Sheets analysis")
    print()

    print("=" * 100)
    print("✅ Complete analysis finished!")
    print("=" * 100)


if __name__ == "__main__":
    main()
