# Memory Analysis Scripts

This directory contains 9 ready-to-use scripts for analyzing memory profiling data collected by tracemalloc.

## 📁 Scripts Overview

| Script | What It Does | Output |
|--------|-------------|--------|
| `01_quick_comparison.py` | Shows snapshot timeline & long-term growth | Console |
| `02_consecutive_comparison.py` | Detects sudden memory increases | Console |
| `03_export_html.py` | Creates HTML report with charts | HTML file + Browser |
| `04_export_speedscope.py` | Creates flamegraph for speedscope.app | JSON file |
| `05_detailed_inspection.py` | Shows detailed snapshot info | Console |
| `06_timeline_graph.py` | Creates visual timeline graph | PNG file |
| `07_export_csv.py` | Exports all snapshots to CSV | CSV file |
| `08_find_leak_candidates.py` | Identifies potential memory leaks | Console |
| `09_complete_analysis.py` | **All-in-one comprehensive analysis** | Console |

## 🚀 How to Run

### Prerequisites

Make sure you have snapshots collected (memory profiling must be enabled):
```bash
# In .env file:
MEMORY_PROFILING_ENABLED=True
```

Snapshots are stored in: `data/memory_snapshots/`

### Running Scripts

**From project root:**

```bash
# Option 1: Quick comparison (recommended to start)
python scripts/memory_analysis/01_quick_comparison.py

# Option 2: Consecutive comparisons (find sudden spikes)
python scripts/memory_analysis/02_consecutive_comparison.py

# Option 3: HTML report (best visualization)
python scripts/memory_analysis/03_export_html.py

# Option 4: Speedscope flamegraph
python scripts/memory_analysis/04_export_speedscope.py

# Option 5: Detailed inspection
python scripts/memory_analysis/05_detailed_inspection.py

# Option 6: Timeline graph (requires matplotlib)
python scripts/memory_analysis/06_timeline_graph.py

# Option 7: Export to CSV
python scripts/memory_analysis/07_export_csv.py

# Option 8: Find leak candidates (important!)
python scripts/memory_analysis/08_find_leak_candidates.py

# Option 9: Complete analysis (recommended!)
python scripts/memory_analysis/09_complete_analysis.py
```

## 📊 Recommended Workflow

### 1. Start with Complete Analysis
```bash
python scripts/memory_analysis/09_complete_analysis.py
```
This gives you an overview of:
- Current memory status
- Growth trends
- Top consumers
- Recommendations

### 2. If Memory Growth Detected, Find Leak Candidates
```bash
python scripts/memory_analysis/08_find_leak_candidates.py
```
This identifies code locations that consistently grow.

### 3. Visualize with HTML Report
```bash
python scripts/memory_analysis/03_export_html.py
```
Creates interactive charts and opens in browser.

### 4. Deep Dive with Speedscope (Optional)
```bash
python scripts/memory_analysis/04_export_speedscope.py
```
Upload the generated `.speedscope.json` to https://speedscope.app for interactive flamegraph.

## 📋 Script Details

### Option 1: Quick Comparison
**Best for:** Quick overview of memory growth over time

**Shows:**
- Timeline of all snapshots
- Comparison of oldest vs newest snapshot
- Memory increase analysis
- Top 10 memory consumers

**Example output:**
```
📅 Snapshot Timeline:
1. 2024-11-16 10:30:00 | Memory:  245.32 MB | Peak:  312.45 MB | Blocks: 45,231
2. 2024-11-16 11:00:00 | Memory:  248.67 MB | Peak:  315.22 MB | Blocks: 46,102

🔍 Long-term Memory Growth Analysis:
Time Period: 5.5 hours
Memory Increase: +12.34 MB (+5.3%)
Analysis: ⚠️ Moderate memory increase - monitor for trends
```

### Option 2: Consecutive Comparison
**Best for:** Finding when memory spikes occurred

**Shows:**
- Comparison between each consecutive snapshot
- Flags significant increases (🚨 High, ⚠️ Moderate, ✅ Normal)
- Top allocation for each spike

**Use when:** You want to pinpoint exactly when memory jumped

### Option 3: Export HTML
**Best for:** Visual analysis with charts

**Creates:**
- Self-contained HTML file
- Interactive bar chart (Chart.js)
- Memory statistics cards
- Detailed allocation table

**Opens automatically in browser**

### Option 4: Export Speedscope
**Best for:** Interactive flamegraph exploration

**Creates:**
- `.speedscope.json` file
- Upload to https://speedscope.app
- Interactive flamegraph visualization

**Great for:** Understanding call stacks and memory distribution

### Option 5: Detailed Inspection
**Best for:** Examining a single snapshot in detail

**Shows:**
- Full snapshot metadata
- Top 30 memory allocations
- Exact file locations and sizes

### Option 6: Timeline Graph
**Best for:** Visual trend analysis

**Requires:** `pip install matplotlib`

**Creates:**
- PNG graph with 2 plots
- Memory usage over time
- Memory blocks over time
- Shows averages and trends

### Option 7: Export CSV
**Best for:** Excel/Sheets analysis or custom processing

**Creates:**
- CSV file with all snapshots
- Columns: Timestamp, Memory, Blocks, Top Allocation, etc.

**Use with:**
- Microsoft Excel
- Google Sheets
- Python pandas

### Option 8: Find Leak Candidates
**Best for:** Identifying memory leaks

**Analyzes:**
- Allocations across multiple snapshots
- Identifies consistently growing locations
- Ranks by growth percentage

**Shows:**
- 🚨 HIGH severity leaks (>50% growth)
- ⚠️ MODERATE leaks (20-50% growth)
- ⚠️ LOW leaks (10-20% growth)

**This is critical for leak detection!**

### Option 9: Complete Analysis
**Best for:** First-time analysis or regular checkups

**Includes:**
- Current memory status
- Growth analysis
- Top consumers
- Recent trends
- Recommendations
- Export suggestions

**This is your go-to script!**

## 🔧 Requirements

### All Scripts (built-in)
- Python 3.12+
- No additional packages needed for most scripts

### Optional (for specific scripts)
```bash
# For Option 6 (timeline graph)
pip install matplotlib
```

## 💡 Tips

### How Often to Run?

- **Daily:** Run Option 9 (Complete Analysis)
- **Weekly:** Export HTML report for archiving
- **When investigating leaks:** Use Options 2, 5, and 8

### Interpreting Results

**Normal memory usage:**
- Small fluctuations (<5%)
- Stable after warmup period
- Predictable growth with load

**Potential memory leak:**
- Continuous upward trend
- Growth doesn't stabilize
- Memory not released after load decreases

**Action thresholds:**
- `<1 MB growth`: ✅ Normal
- `1-10 MB growth`: ⚠️ Monitor
- `10-50 MB growth`: ⚠️ Investigate
- `>50 MB growth`: 🚨 Critical - immediate action

## 🔍 When You Detect a Leak

1. **Run Option 8** to identify leak candidates
2. **Run Option 3** to generate HTML report
3. **Review the code** at flagged locations
4. **Check for:**
   - Unclosed file handles
   - Growing caches without limits
   - Event listeners not removed
   - Circular references
   - Database connections not closed

5. **Use memray for deep analysis:**
   ```bash
   pip install memray
   memray run -o trace.bin uvicorn codemie.rest_api.main:app
   # Let it run during leak reproduction
   memray flamegraph trace.bin
   ```

## 📈 Example Workflow

```bash
# 1. Check current status
python scripts/memory_analysis/09_complete_analysis.py

# 2. If growth detected, find suspects
python scripts/memory_analysis/08_find_leak_candidates.py

# 3. Create visual report
python scripts/memory_analysis/03_export_html.py

# 4. Export for deeper analysis
python scripts/memory_analysis/04_export_speedscope.py
# Upload to https://speedscope.app

# 5. Create archive
python scripts/memory_analysis/07_export_csv.py
```

## 🆘 Troubleshooting

**No snapshots found:**
- Check `MEMORY_PROFILING_ENABLED=True` in `.env`
- Restart the application
- Wait for first snapshot (default: 30 minutes)

**Scripts fail to import:**
- Make sure you're running from project root
- Check virtual environment is activated

**matplotlib not found (Option 6):**
```bash
pip install matplotlib
```

## 📚 Additional Resources

- **tracemalloc docs:** https://docs.python.org/3/library/tracemalloc.html
- **memray:** https://bloomberg.github.io/memray/
- **Speedscope:** https://www.speedscope.app/

## 🎯 Quick Reference

**Most useful for leak detection:**
1. Option 9 (Complete Analysis) - Start here
2. Option 8 (Find Leak Candidates) - Identify suspects
3. Option 3 (HTML Report) - Visualize
4. Option 2 (Consecutive) - Find when it happened

**Best exports:**
- HTML: Option 3
- Speedscope: Option 4
- CSV: Option 7