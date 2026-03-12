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
Option 5: Detailed Snapshot Inspection

Shows detailed information about the latest snapshot including all top allocations.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from memory_analysis_helpers import get_recent_snapshots


def main():
    print("=" * 100)
    print("🔍 OPTION 5: Detailed Snapshot Inspection")
    print("=" * 100)
    print()

    # Get latest snapshot
    snapshots = get_recent_snapshots(limit=1)

    if not snapshots:
        print("❌ No snapshots found!")
        sys.exit(1)

    snapshot = snapshots[0]

    print(f"📊 Snapshot ID: {snapshot.id}")
    print("=" * 100)
    print(f"⏰ Timestamp:        {snapshot.timestamp}")
    print(f"📝 Description:      {snapshot.description}")
    print(f"💾 Current Memory:   {snapshot.current_memory_mb:.2f} MB")
    print(f"📈 Peak Memory:      {snapshot.peak_memory_mb:.2f} MB")
    print(f"📦 Traced Blocks:    {snapshot.traced_memory_blocks:,}")
    print(f"🔝 Top Allocations:  {len(snapshot.top_allocations)}")
    print()

    print("🔝 Top 30 Memory Allocations:")
    print("=" * 100)
    print(f"{'Rank':<6} {'Size (MB)':<12} {'Count':<10} {'Location':<70}")
    print("-" * 100)

    for i, alloc in enumerate(snapshot.top_allocations[:30], 1):
        size = alloc.get("size_mb", 0.0)
        count = alloc.get("count", 0)
        location = alloc.get("location", "unknown")

        # Truncate location if too long
        if len(location) > 70:
            location = location[:67] + "..."

        print(f"{i:<6} {size:<12.4f} {count:<10,} {location}")

    print()

    # Calculate total from top allocations
    total_top = sum(alloc.get("size_mb", 0.0) for alloc in snapshot.top_allocations[:30])

    # Calculate percentage with explicit division by zero handling
    if snapshot.current_memory_mb > 0:
        percentage = (total_top / snapshot.current_memory_mb) * 100
    else:
        percentage = 0.0

    print(f"📊 Top 30 allocations account for: {total_top:.2f} MB ({percentage:.1f}% of total)")
    print()
    print("✅ Detailed inspection complete!")


if __name__ == "__main__":
    main()
