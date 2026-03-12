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
Option 8: Find Memory Leak Candidates

Analyzes snapshots to find locations that consistently grow in memory usage.
This helps identify potential memory leaks.
"""

import sys
from collections import defaultdict
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from memory_analysis_helpers import get_recent_snapshots


def main():
    print("=" * 100)
    print("🔍 OPTION 8: Find Memory Leak Candidates")
    print("=" * 100)
    print()

    # Get recent snapshots
    snapshots = get_recent_snapshots(limit=20)

    if len(snapshots) < 3:
        print("❌ Need at least 3 snapshots to detect trends!")
        sys.exit(1)

    print(f"📁 Analyzing {len(snapshots)} snapshots for memory leak patterns...")
    print()

    # Track allocations across snapshots
    location_growth = defaultdict(list)

    for snap in reversed(snapshots):  # Oldest first
        for alloc in snap.top_allocations:
            location = alloc["location"]
            location_growth[location].append((snap.timestamp, alloc["size_mb"]))

    # Find consistently growing locations
    print("🔍 Potential Memory Leak Locations:")
    print("=" * 100)
    print()

    leak_candidates = []

    for location, data in location_growth.items():
        if len(data) >= 3:  # Appears in at least 3 snapshots
            sizes = [size for _, size in data]

            # Check if generally growing
            # Guard against division by zero
            first_half_len = len(sizes) // 2
            second_half_len = len(sizes) - first_half_len

            if first_half_len == 0 or second_half_len == 0 or len(sizes) == 0:
                continue  # Skip if data is insufficient

            first_half_avg = sum(sizes[:first_half_len]) / first_half_len
            second_half_avg = sum(sizes[first_half_len:]) / second_half_len

            if second_half_avg > first_half_avg * 1.1:  # 10% growth
                growth = sizes[-1] - sizes[0]

                # Calculate growth percentage with explicit division by zero handling
                if sizes[0] != 0:
                    growth_pct = (growth / sizes[0]) * 100
                else:
                    growth_pct = 0.0

                avg_size = sum(sizes) / len(sizes)

                leak_candidates.append(
                    {
                        "location": location,
                        "appearances": len(sizes),
                        "avg_size": avg_size,
                        "growth": growth,
                        "growth_pct": growth_pct,
                        "first_size": sizes[0],
                        "last_size": sizes[-1],
                    }
                )

    # Sort by growth percentage
    leak_candidates.sort(key=lambda x: x["growth_pct"], reverse=True)

    if leak_candidates:
        for i, candidate in enumerate(leak_candidates[:15], 1):
            severity = (
                "🚨 HIGH"
                if candidate["growth_pct"] > 50
                else "⚠️  MODERATE"
                if candidate["growth_pct"] > 20
                else "⚠️  LOW"
            )

            print(f"{severity} - Leak Candidate #{i}")
            print(f"   Location:    {candidate['location'][:75]}")
            print(f"   Appearances: {candidate['appearances']} snapshots")
            print(f"   Average:     {candidate['avg_size']:.2f} MB")
            print(f"   Growth:      {candidate['first_size']:.2f} MB → {candidate['last_size']:.2f} MB")
            print(f"   Change:      +{candidate['growth']:.2f} MB (+{candidate['growth_pct']:.1f}%)")
            print()

        print(f"✅ Found {len(leak_candidates)} potential leak locations")
        print()
        print("💡 Next steps:")
        print("   1. Review the code at these locations")
        print("   2. Check for unclosed resources (files, connections, etc.)")
        print("   3. Look for growing collections (lists, dicts, caches)")
        print("   4. Verify proper cleanup in error handlers")
        print("   5. Use memray for detailed analysis: memray run -o trace.bin uvicorn ...")
    else:
        print("✅ No obvious memory leak patterns detected!")
        print("   All allocations appear stable.")
        print()
        print("💡 If you still suspect a leak:")
        print("   • Let the application run longer to collect more snapshots")
        print("   • Use Option 1 or 2 to check for overall memory growth")
        print("   • Use memray for deep analysis")


if __name__ == "__main__":
    main()
