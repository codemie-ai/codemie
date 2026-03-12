# Memory Analysis Quick Start Guide

## ✅ All Set! You have 9 analysis scripts ready to use.

Your snapshots are located in: `data/memory_snapshots/`

## 🚀 Run These Commands One by One

**From project root directory:** `/Users/demetredzmanashvili/Projects/codemie/codemie`

---

### 1️⃣ RECOMMENDED: Start with Complete Analysis

```bash
python scripts/memory_analysis/09_complete_analysis.py
```

**What you get:**
- ✅ Current memory status
- ✅ Growth analysis over time
- ✅ Top memory consumers
- ✅ Recent trends
- ✅ Actionable recommendations

**This is your go-to script for regular checkups!**

---

### 2️⃣ Quick Timeline Overview

```bash
python scripts/memory_analysis/01_quick_comparison.py
```

**What you get:**
- ✅ List of all snapshots with timestamps
- ✅ Comparison of oldest vs newest
- ✅ Memory increase percentage
- ✅ Top 10 memory consumers

---

### 3️⃣ Find When Memory Spiked

```bash
python scripts/memory_analysis/02_consecutive_comparison.py
```

**What you get:**
- ✅ Snapshot-by-snapshot comparison
- ✅ Flags: 🚨 High increase | ⚠️ Moderate | ✅ Normal
- ✅ Pinpoint exact time of memory spikes

---

### 4️⃣ Generate HTML Report (Best Visualization!)

```bash
python scripts/memory_analysis/03_export_html.py
```

**What you get:**
- ✅ Interactive HTML report with charts
- ✅ Opens automatically in browser
- ✅ Beautiful visualizations with Chart.js
- ✅ Share with team members

**Output:** `data/memory_snapshots/report_<id>.html`

---

### 5️⃣ Create Flamegraph for Speedscope

```bash
python scripts/memory_analysis/04_export_speedscope.py
```

**What you get:**
- ✅ `.speedscope.json` file
- ✅ Upload to https://speedscope.app
- ✅ Interactive flamegraph exploration

**Output:** `data/memory_snapshots/speedscope_<id>.speedscope.json`

Then: Go to https://speedscope.app and drag-drop the file!

---

### 6️⃣ Detailed Single Snapshot Inspection

```bash
python scripts/memory_analysis/05_detailed_inspection.py
```

**What you get:**
- ✅ Full snapshot details
- ✅ Top 30 memory allocations
- ✅ Exact file locations and sizes

---

### 7️⃣ Create Visual Timeline Graph

```bash
python scripts/memory_analysis/06_timeline_graph.py
```

**Requires:** `pip install matplotlib` (if not installed)

**What you get:**
- ✅ PNG graph with memory and blocks over time
- ✅ Trend lines and averages
- ✅ Visual identification of growth patterns

**Output:** `data/memory_snapshots/memory_timeline.png`

---

### 8️⃣ Export to CSV for Excel/Sheets

```bash
python scripts/memory_analysis/07_export_csv.py
```

**What you get:**
- ✅ CSV file with all snapshots
- ✅ Open in Excel, Google Sheets, or pandas
- ✅ Custom analysis and pivot tables

**Output:** `data/memory_snapshots/snapshots_summary.csv`

---

### 9️⃣ IMPORTANT: Find Memory Leak Candidates

```bash
python scripts/memory_analysis/08_find_leak_candidates.py
```

**What you get:**
- ✅ Identifies code locations that consistently grow
- ✅ Ranks by severity: 🚨 HIGH | ⚠️ MODERATE | ⚠️ LOW
- ✅ Growth percentages and file locations

**Use this when you detect memory growth!**

---

## 🎯 Recommended Analysis Workflow

### For Daily/Weekly Monitoring:

```bash
# 1. Quick health check
python scripts/memory_analysis/09_complete_analysis.py
```

### When Memory Growth Detected:

```bash
# 1. Complete analysis
python scripts/memory_analysis/09_complete_analysis.py

# 2. Find leak suspects
python scripts/memory_analysis/08_find_leak_candidates.py

# 3. Generate visual report
python scripts/memory_analysis/03_export_html.py

# 4. Create flamegraph
python scripts/memory_analysis/04_export_speedscope.py
```

### For Reporting/Documentation:

```bash
# Export HTML report
python scripts/memory_analysis/03_export_html.py

# Export CSV for spreadsheets
python scripts/memory_analysis/07_export_csv.py

# Create timeline graph
python scripts/memory_analysis/06_timeline_graph.py
```

---

## 📊 Understanding the Output

### Memory Growth Thresholds:

| Growth | Status | Action |
|--------|--------|--------|
| `<1 MB` | ✅ **Normal** | Continue monitoring |
| `1-10 MB` | ⚠️ **Monitor** | Check weekly |
| `10-50 MB` | ⚠️ **Investigate** | Run leak detection |
| `>50 MB` | 🚨 **Critical** | Immediate action required |

### Growth Percentage Thresholds:

| % Growth | Status | Action |
|----------|--------|--------|
| `<5%` | ✅ **Normal** | OK |
| `5-20%` | ⚠️ **Monitor** | Watch trends |
| `20-50%` | ⚠️ **Investigate** | Find suspects |
| `>50%` | 🚨 **Critical** | Memory leak likely |

---

## 🔍 What to Do When You Find a Leak

1. **Run the leak finder:**
   ```bash
   python scripts/memory_analysis/08_find_leak_candidates.py
   ```

2. **Review the flagged code locations**
   - Check for unclosed resources (files, DB connections)
   - Look for growing collections (lists, dicts, caches)
   - Verify cleanup in error handlers

3. **Use memray for deep analysis:**
   ```bash
   pip install memray
   memray run -o trace.bin uvicorn codemie.rest_api.main:app
   # Let it run while reproducing the leak
   # Ctrl+C to stop
   memray flamegraph trace.bin
   ```

4. **Common leak patterns:**
   - ❌ Caches without size limits or TTL
   - ❌ Event listeners not removed
   - ❌ File handles not closed
   - ❌ Database connections not returned to pool
   - ❌ Circular references preventing GC

---

## 💡 Pro Tips

### Run analyses at the same time each day
This gives you consistent comparisons (same load patterns).

### Keep snapshots for at least 7 days
Helps identify weekly patterns and trends.

### Export HTML reports weekly
Create a historical archive for trend analysis.

### Use Option 9 as your daily health check
Quick, comprehensive, actionable.

### Investigate any >10 MB growth immediately
Don't wait for it to become critical.

---

## 🆘 Troubleshooting

### "No snapshots found"
- ✅ Check `.env` has `MEMORY_PROFILING_ENABLED=True`
- ✅ Restart the application
- ✅ Wait for first snapshot (default: 30 min)

### "ImportError: No module named..."
- ✅ Make sure you're in the project root
- ✅ Run from: `/Users/demetredzmanashvili/Projects/codemie/codemie`

### Option 6 fails (matplotlib)
```bash
pip install matplotlib
```

---

## 📚 More Information

See `README.md` in this directory for:
- Detailed script descriptions
- Advanced usage examples
- Interpretation guidelines
- Additional resources

---

## 🎉 You're All Set!

Start with:
```bash
python scripts/memory_analysis/09_complete_analysis.py
```

Happy memory leak hunting! 🔍