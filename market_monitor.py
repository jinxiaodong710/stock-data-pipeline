#!/usr/bin/env python3
"""市场监控 - 每15分钟分析快照，输出涨幅榜/异动/量能警报"""
import duckdb, sys, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DB = Path.home() / 'go' / 'data' / 'snapshots.duckdb'

con = None
for attempt in range(3):
    try:
        con = duckdb.connect(str(DB), read_only=True)
        break
    except:
        time.sleep(1)
if not con:
    print("DB_LOCKED")
    sys.exit(0)

latest = con.execute("SELECT max(created_at) FROM snapshots").fetchone()[0]
if not latest:
    print("NO_DATA")
    sys.exit(0)
window_start = latest - timedelta(seconds=15)

lines = [f"📊 实时监控 | {latest.astimezone(CST):%H:%M:%S}", ""]

# 1. 涨停板（≥20% 创/科 or ≥10% 主板）
rows = con.execute("""
    SELECT code, name, last, pct_change, turnover
    FROM snapshots WHERE created_at BETWEEN ? AND ?
    AND pct_change >= 20
    ORDER BY pct_change DESC LIMIT 10
""", [window_start, latest]).fetchall()

if rows:
    lines.append("🔥 涨停封板 (≥20%)：")
    for r in rows:
        lines.append(f"  {r[1]}({r[0]}) {r[2]:.2f} +{r[3]:.1f}% 换手{r[4]:.1f}%")

# 2. 大涨区 10~20%
rows = con.execute("""
    SELECT code, name, last, pct_change, turnover
    FROM snapshots WHERE created_at BETWEEN ? AND ?
    AND pct_change >= 10 AND pct_change < 20
    ORDER BY pct_change DESC LIMIT 10
""", [window_start, latest]).fetchall()

if rows:
    lines.append("")
    lines.append("📈 大涨 10~20%：")
    for r in rows:
        lines.append(f"  {r[1]}({r[0]}) {r[2]:.2f} +{r[3]:.1f}% 换手{r[4]:.1f}%")

# 3. 跌幅榜
rows = con.execute("""
    SELECT code, name, last, pct_change, turnover
    FROM snapshots WHERE created_at BETWEEN ? AND ?
    ORDER BY pct_change ASC LIMIT 5
""", [window_start, latest]).fetchall()

lines.append("")
lines.append("❄️ 跌幅 TOP 5：")
for r in rows:
    chg = f"{r[3]:.1f}%"
    lines.append(f"  {r[1]}({r[0]}) {r[2]:.2f} {chg} 换手{r[4]:.1f}%")

# 5. 高换手异动
rows = con.execute("""
    SELECT code, name, last, pct_change, turnover
    FROM snapshots WHERE created_at BETWEEN ? AND ? AND turnover > 20 AND pct_change > 5
    ORDER BY turnover DESC LIMIT 5
""", [window_start, latest]).fetchall()

if rows:
    lines.append("")
    lines.append("⚡ 高换手异动：")
    for r in rows:
        lines.append(f"  {r[1]}({r[0]}) {r[2]:.2f} +{r[3]:.1f}% 换手{r[4]:.1f}%")

# 6. 自选
watchlist = ['301596', '603179']
placeholders = ','.join(['?']*len(watchlist))
rows = con.execute(f"""
    SELECT code, name, last, pct_change, turnover
    FROM snapshots WHERE created_at BETWEEN ? AND ? AND code IN ({placeholders})
    ORDER BY pct_change DESC
""", [window_start, latest] + watchlist).fetchall()
if rows:
    lines.append("")
    lines.append("⭐ 你的票：")
    for r in rows:
        chg = f"+{r[3]:.1f}%" if r[3] >= 0 else f"{r[3]:.1f}%"
        lines.append(f"  {r[1]}({r[0]}) {r[2]:.2f} {chg} 换手{r[4]:.1f}%")

con.close()
print('\n'.join(lines))
