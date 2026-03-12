# How to Use Memory Analysis Scripts

## 🎯 Quick Start (Updated for Gzip-Compressed Snapshots)

The memory profiling service now stores snapshots as **gzip-compressed files** (`.json.gz`) in cloud storage or local filesystem. Scripts automatically handle decompression.

---

## 📂 Step 1: Get Your Snapshots

### **Option A: Local Development (Filesystem Storage)**

Snapshots are automatically saved to:
```
./codemie-storage/monitoring/memory_snapshots/
```

The scripts look for snapshots in:
```
./data/memory_snapshots/
```

**Create a symlink or copy files:**

```bash
# From project root
cd /Users/demetredzmanashvili/Projects/codemie/codemie

# Create the data directory if it doesn't exist
mkdir -p data/memory_snapshots

# Option 1: Symlink (recommended for local dev)
ln -sf "$(pwd)/codemie-storage/monitoring/memory_snapshots/"* data/memory_snapshots/

# Option 2: Copy files
cp codemie-storage/monitoring/memory_snapshots/*.json.gz data/memory_snapshots/
```

### **Option B: Production (Cloud Storage - S3/Azure/GCP)**

Download snapshots from your cloud storage:

**AWS S3:**
```bash
# Download all snapshots
aws s3 sync s3://your-bucket/monitoring/memory_snapshots/ data/memory_snapshots/

# Or download specific files
aws s3 cp s3://your-bucket/monitoring/memory_snapshots/snapshot_2025-11-20_14-30-45_abc12345.json.gz data/memory_snapshots/
```

**Azure Blob Storage:**
```bash
az storage blob download-batch \
  --source monitoring/memory_snapshots \
  --destination data/memory_snapshots/ \
  --account-name your-account
```

**GCP Cloud Storage:**
```bash
gsutil -m cp -r gs://your-bucket/monitoring/memory_snapshots/* data/memory_snapshots/
```

---

## 🚀 Step 2: Run Analysis Scripts

### **Quick Health Check (Start Here!)**

```bash
# From project root
python scripts/memory_analysis/09_complete_analysis.py
```

This gives you:
- ✅ Current memory status
- ✅ Growth analysis
- ✅ Top consumers
- ✅ Actionable recommendations

---

### **All Available Scripts**

| Script | Purpose | Output |
|--------|---------|--------|
| `01_quick_comparison.py` | Timeline and oldest vs newest comparison | Console |
| `02_consecutive_comparison.py` | Snapshot-by-snapshot comparison | Console |
| `03_export_html.py` | Generate HTML report with charts | `data/memory_snapshots/report_*.html` |
| `04_export_speedscope.py` | Export for flamegraph visualization | `data/memory_snapshots/speedscope_*.json` |
| `05_detailed_inspection.py` | Detailed single snapshot inspection | Console |
| `06_timeline_graph.py` | Visual timeline graph (requires matplotlib) | `data/memory_snapshots/memory_timeline.png` |
| `07_export_csv.py` | Export to CSV for Excel/Sheets | `data/memory_snapshots/snapshots_summary.csv` |
| `08_find_leak_candidates.py` | Find memory leak suspects | Console |
| `09_complete_analysis.py` | Comprehensive analysis (recommended) | Console |

---

## 📊 Example Workflow

### **Daily Monitoring:**

```bash
# Quick health check
python scripts/memory_analysis/09_complete_analysis.py
```

### **When You Detect Growth:**

```bash
# 1. Complete analysis
python scripts/memory_analysis/09_complete_analysis.py

# 2. Find leak suspects
python scripts/memory_analysis/08_find_leak_candidates.py

# 3. Generate visual HTML report
python scripts/memory_analysis/03_export_html.py
# Opens in browser automatically

# 4. Create flamegraph for deep dive
python scripts/memory_analysis/04_export_speedscope.py
# Upload the .speedscope.json file to https://speedscope.app
```

### **For Reports/Documentation:**

```bash
# Export HTML (shareable)
python scripts/memory_analysis/03_export_html.py

# Export CSV (for spreadsheets)
python scripts/memory_analysis/07_export_csv.py

# Create timeline graph (visual)
python scripts/memory_analysis/06_timeline_graph.py
```

---

## 🔍 Understanding the Output

### **Memory Growth Indicators:**

| Growth | Status | Action |
|--------|--------|--------|
| `<1 MB` | ✅ Normal | Continue monitoring |
| `1-10 MB` | ⚠️ Monitor | Check weekly |
| `10-50 MB` | ⚠️ Investigate | Run leak detection |
| `>50 MB` | 🚨 Critical | Immediate action |

### **Example Output:**

```
📊 Memory Snapshot Timeline (most recent first):
 1. 2025-11-20 21:05:30 | Memory:   245.32 MB | Peak:   250.10 MB | Blocks: 123,456
 2. 2025-11-20 20:35:15 | Memory:   242.15 MB | Peak:   248.90 MB | Blocks: 122,890
 3. 2025-11-20 20:05:00 | Memory:   238.50 MB | Peak:   245.20 MB | Blocks: 121,234

🔍 Long-term Memory Growth Analysis:
📊 Time Period: 2.5 hours
📈 Memory Increase: 6.82 MB (2.9%)
📦 Blocks Increase: 2,222

💡 Analysis: ✅ Minimal memory increase (<1 MB) - normal operation.

🔥 Top 10 Memory Consumers:
 1.    15.23 MB |  12,345 allocs | /src/codemie/agents/tools/code_tools.py:125
 2.    12.45 MB |   8,901 allocs | /src/codemie/service/assistant_service.py:78
 3.    10.12 MB |   5,432 allocs | /src/langchain/chains/base.py:234
```

---

## 🐛 Troubleshooting

### **"No snapshots found!"**

**Check if snapshots exist:**
```bash
ls -la data/memory_snapshots/
```

**Should see files like:**
```
snapshot_2025-11-20_21-05-30_abc12345.json.gz
snapshot_2025-11-20_20-35-15_def67890.json.gz
```

**If empty:**
1. Check memory profiling is enabled: `MEMORY_PROFILING_ENABLED=True` in `.env`
2. Restart your application
3. Wait for first snapshot (default: 5 minutes based on your config)
4. Check logs for snapshot creation messages

**For production/cloud storage:**
- Download files from S3/Azure/GCP (see Step 1 above)

### **"Failed to load snapshot"**

The scripts expect `.json.gz` (gzip-compressed) files. If you have old uncompressed `.json` files, they won't work. Only use snapshots created by the updated service.

### **"ImportError: No module named matplotlib"**

Only needed for script #6 (timeline graph):
```bash
source .venv/bin/activate
poetry add matplotlib
# or
pip install matplotlib
```

### **Scripts show errors about decompression**

Make sure you're running the latest version of the scripts. They should automatically handle gzip decompression.

---

## 💡 Pro Tips

### **1. Automate Daily Reports**

Add to crontab:
```bash
0 9 * * * cd /path/to/codemie && python scripts/memory_analysis/09_complete_analysis.py > /tmp/memory_report.txt && mail -s "Daily Memory Report" you@example.com < /tmp/memory_report.txt
```

### **2. Set Up Alerts**

Create a wrapper script:
```python
import subprocess
import sys

result = subprocess.run(["python", "scripts/memory_analysis/09_complete_analysis.py"],
                       capture_output=True, text=True)

if "🚨 CRITICAL" in result.stdout or "LIKELY MEMORY LEAK" in result.stdout:
    # Send alert (email, Slack, PagerDuty, etc.)
    print("ALERT: Memory leak detected!")
    sys.exit(1)
```

### **3. Keep Historical Reports**

```bash
# Export HTML with date
python scripts/memory_analysis/03_export_html.py
cp data/memory_snapshots/report_*.html reports/report_$(date +%Y-%m-%d).html
```

### **4. Compare Week-over-Week**

Keep snapshots for at least 7 days to identify weekly patterns (e.g., Monday spikes, weekend drops).

---

## 📁 File Structure

```
codemie/
├── codemie-storage/
│   └── monitoring/
│       └── memory_snapshots/           # Local storage (filesystem mode)
│           ├── snapshot_*.json.gz      # Compressed snapshots
│           └── snapshot_*.json.gz
├── data/
│   └── memory_snapshots/               # Where scripts look for files
│       └── (symlink or downloaded files)
├── scripts/
│   └── memory_analysis/
│       ├── 01_quick_comparison.py
│       ├── 02_consecutive_comparison.py
│       ├── ...
│       └── 09_complete_analysis.py
└── .env                                # MEMORY_PROFILING_ENABLED=True
```

---

## 🎉 You're Ready!

**Start with this command:**
```bash
python scripts/memory_analysis/09_complete_analysis.py
```

If you see snapshots and analysis, you're all set! 🚀

For detailed explanations of each script, see `QUICKSTART.md` or `README.md` in this directory.