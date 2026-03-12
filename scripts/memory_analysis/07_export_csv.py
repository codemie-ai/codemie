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
Option 7: Export All Snapshots to CSV

Exports all snapshots to CSV format for analysis in Excel, Google Sheets, or pandas.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import csv

from memory_analysis_helpers import get_recent_snapshots


def main():
    print("=" * 100)
    print("📊 OPTION 7: Export All Snapshots to CSV")
    print("=" * 100)
    print()

    # Get all snapshots
    snapshots = get_recent_snapshots(limit=1000)

    if not snapshots:
        print("❌ No snapshots found!")
        sys.exit(1)

    print(f"📁 Found {len(snapshots)} snapshots")
    print("📝 Exporting to CSV...")
    print()

    # Export to CSV
    csv_path = Path("data/memory_snapshots/snapshots_summary.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "Timestamp",
                    "Snapshot ID",
                    "Description",
                    "Current Memory (MB)",
                    "Peak Memory (MB)",
                    "Traced Blocks",
                    "Top Allocation (MB)",
                    "Top Allocation Location",
                ]
            )

            for snap in reversed(snapshots):  # Oldest first
                if snap.top_allocations and len(snap.top_allocations) > 0:
                    top_alloc_size = snap.top_allocations[0].get("size_mb", 0.0)
                    top_alloc_location = snap.top_allocations[0].get("location", "N/A")
                else:
                    top_alloc_size = 0
                    top_alloc_location = "N/A"

                writer.writerow(
                    [
                        snap.timestamp,
                        snap.id,
                        snap.description,
                        f"{snap.current_memory_mb:.2f}",
                        f"{snap.peak_memory_mb:.2f}",
                        snap.traced_memory_blocks,
                        f"{top_alloc_size:.4f}",
                        top_alloc_location,
                    ]
                )
    except IOError as e:
        print(f"❌ Error writing CSV file: {e}")
        print(f"   Path: {csv_path}")
        print("   Check disk space and file permissions.")
        sys.exit(1)

    print("✅ CSV exported successfully!")
    print(f"   📁 File: {csv_path}")
    print()
    print(f"📊 Exported {len(snapshots)} snapshots with columns:")
    print("   • Timestamp")
    print("   • Snapshot ID")
    print("   • Description")
    print("   • Current Memory (MB)")
    print("   • Peak Memory (MB)")
    print("   • Traced Blocks")
    print("   • Top Allocation (MB)")
    print("   • Top Allocation Location")
    print()
    print("💡 Open with:")
    print("   • Microsoft Excel")
    print("   • Google Sheets")
    print("   • Python pandas: pd.read_csv('snapshots_summary.csv')")
    print()
    print("✅ Done!")


if __name__ == "__main__":
    main()
