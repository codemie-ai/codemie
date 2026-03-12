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
Option 2: Consecutive Snapshot Comparison

Compares consecutive snapshots to find sudden memory increases (useful for detecting recent leaks).
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from memory_analysis_helpers import get_recent_snapshots, compare_snapshots


def main():
    print("=" * 100)
    print("📈 OPTION 2: Consecutive Snapshot Comparison")
    print("=" * 100)
    print()

    # Compare consecutive snapshots to find sudden increases
    snapshots = get_recent_snapshots(limit=20)

    if len(snapshots) < 2:
        print("❌ Need at least 2 snapshots for comparison!")
        sys.exit(1)

    print(f"📁 Analyzing {len(snapshots)} snapshots for sudden memory increases...\n")
    print("Legend: 🚨 = High increase (>5 MB) | ⚠️  = Moderate (>1 MB) | ✅ = Normal (<1 MB)")
    print("=" * 100)
    print()

    for i in range(len(snapshots) - 1):
        newer = snapshots[i]
        older = snapshots[i + 1]

        diff = compare_snapshots(older.id, newer.id)

        # Flag significant increases
        if diff.memory_increase_mb > 5:  # More than 5 MB increase
            flag = "🚨"
            status = "HIGH"
        elif diff.memory_increase_mb > 1:
            flag = "⚠️ "
            status = "MODERATE"
        else:
            flag = "✅"
            status = "NORMAL"

        print(f"{flag} [{status:8s}] {older.timestamp[:19]} → {newer.timestamp[:19]}")
        print(f"   Memory Change: {diff.memory_increase_mb:+7.2f} MB ({diff.memory_increase_percentage:+6.1f}%)")
        print(f"   Blocks Change: {diff.blocks_increase:+8,}")

        # Show top allocation if significant increase
        if diff.memory_increase_mb > 1 and diff.top_increases:
            top = diff.top_increases[0]
            print(f"   Top Consumer:  {top.location[:70]}")

        print()

    print("✅ Consecutive comparison complete!")


if __name__ == "__main__":
    main()
