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
Option 4: Export to Speedscope Format

Exports snapshot to Speedscope JSON format for interactive flamegraph visualization.
Upload the generated file to https://speedscope.app
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from memory_analysis_helpers import get_recent_snapshots, export_to_speedscope


def main():
    print("=" * 100)
    print("🔥 OPTION 4: Export to Speedscope Flamegraph")
    print("=" * 100)
    print()

    # Get latest snapshot
    snapshots = get_recent_snapshots(limit=1)

    if not snapshots:
        print("❌ No snapshots found!")
        sys.exit(1)

    snapshot = snapshots[0]
    print(f"🔥 Generating Speedscope flamegraph for snapshot: {snapshot.id[:8]}")
    print(f"   Timestamp: {snapshot.timestamp}")
    print(f"   Memory: {snapshot.current_memory_mb:.2f} MB")
    print()

    # Export to Speedscope format
    speedscope_path = export_to_speedscope(snapshot.id)

    if speedscope_path:
        print("✅ Speedscope file generated successfully!")
        print(f"   📁 File: {speedscope_path}")
        print()
        print("🔥 How to visualize:")
        print("   1. Go to: https://speedscope.app")
        print("   2. Drag & drop the .speedscope.json file into the browser")
        print("   3. Explore the interactive flamegraph!")
        print()
        print("💡 Flamegraph tips:")
        print("   • Wider bars = more memory consumed")
        print("   • Click bars to zoom in")
        print("   • Hover for details")
        print("   • Use search to find specific functions")
        print()
        print(f"✅ File ready: {speedscope_path}")
    else:
        print("❌ Failed to generate Speedscope file")
        sys.exit(1)


if __name__ == "__main__":
    main()
