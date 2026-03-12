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
Option 6: Create Memory Timeline Graph

Creates a visual timeline graph showing memory usage and blocks over time.
Requires: pip install matplotlib
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from datetime import datetime

from memory_analysis_helpers import get_recent_snapshots

# Try to import matplotlib
try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
except ImportError:
    print("❌ matplotlib is not installed!")
    print("   Install it with: pip install matplotlib")
    sys.exit(1)


def main():
    print("=" * 100)
    print("📊 OPTION 6: Create Memory Timeline Graph")
    print("=" * 100)
    print()

    # Get all snapshots
    snapshots = get_recent_snapshots(limit=100)

    if len(snapshots) < 2:
        print("❌ Need at least 2 snapshots to create a timeline!")
        sys.exit(1)

    print(f"📁 Found {len(snapshots)} snapshots")
    snapshots.reverse()  # Oldest first for timeline

    # Extract data
    timestamps = [datetime.fromisoformat(s.timestamp) for s in snapshots]
    memory_mb = [s.current_memory_mb for s in snapshots]
    peak_mb = [s.peak_memory_mb for s in snapshots]
    blocks = [s.traced_memory_blocks for s in snapshots]

    print("📈 Creating timeline graph...")
    print()

    # Create figure with 2 subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
    fig.suptitle("CodeMie Memory Profiling Timeline", fontsize=16, fontweight="bold")

    # Memory over time
    ax1.plot(timestamps, memory_mb, marker="o", linewidth=2, markersize=5, label="Current Memory", color="#2E86DE")
    ax1.plot(
        timestamps, peak_mb, marker="^", linewidth=2, markersize=4, label="Peak Memory", color="#EE5A6F", alpha=0.7
    )
    ax1.set_title("Memory Usage Over Time", fontsize=14, fontweight="bold")
    ax1.set_ylabel("Memory (MB)", fontsize=12)
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3, linestyle="--")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha="right")

    # Add statistics to first plot
    if len(memory_mb) > 0:
        avg_mem = sum(memory_mb) / len(memory_mb)
        max_mem = max(memory_mb)
        min_mem = min(memory_mb)
    else:
        avg_mem = max_mem = min_mem = 0.0
    ax1.axhline(y=avg_mem, color="green", linestyle=":", alpha=0.5, label=f"Avg: {avg_mem:.1f} MB")
    ax1.text(
        timestamps[-1],
        avg_mem,
        f" Avg: {avg_mem:.1f} MB",
        verticalalignment="bottom",
        fontsize=9,
        color="green",
    )

    # Blocks over time
    ax2.plot(timestamps, blocks, marker="s", linewidth=2, markersize=5, color="#F79F1F")
    ax2.set_title("Memory Blocks Over Time", fontsize=14, fontweight="bold")
    ax2.set_xlabel("Time", fontsize=12)
    ax2.set_ylabel("Number of Blocks", fontsize=12)
    ax2.grid(True, alpha=0.3, linestyle="--")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")

    # Add statistics to second plot
    avg_blocks = sum(blocks) / len(blocks)
    ax2.axhline(y=avg_blocks, color="orange", linestyle=":", alpha=0.5)
    ax2.text(
        timestamps[-1],
        avg_blocks,
        f" Avg: {avg_blocks:,.0f} blocks",
        verticalalignment="bottom",
        fontsize=9,
        color="orange",
    )

    plt.tight_layout()

    # Save graph
    output_path = Path("data/memory_snapshots/memory_timeline.png")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(output_path), dpi=150, bbox_inches="tight")
    print(f"✅ Timeline graph saved: {output_path}")
    print()
    print("📊 Statistics:")
    print(f"   Memory: Avg={avg_mem:.2f} MB, Min={min_mem:.2f} MB, Max={max_mem:.2f} MB")
    print(f"   Range: {max_mem - min_mem:.2f} MB")
    print(f"   Blocks: Avg={avg_blocks:,.0f}")
    print()

    # Show graph
    print("🖼️  Displaying graph...")
    plt.show()


if __name__ == "__main__":
    main()
