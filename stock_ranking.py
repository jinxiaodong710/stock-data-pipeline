#!/usr/bin/env python3
"""A股实时排行榜 — 个股(Redis L1) + 板块(首尔Tushare/本地缓存)"""
import redis, sys, re, subprocess, os
from datetime import datetime, timezone, timedelta
CST = timezone(timedelta(hours=8))

CACHE_FILE = '/tmp/sector_cache.txt'

def is_trading():
    now = datetime.now(CST)
    t = now.hour * 60 + now.minute
    return (570 <= t <= 690) or (780 <= t <= 900)

def get_rankings():
    r = redis.Redis(host='localhost', port=6379, db=0)
    keys = r.keys('*')
    stocks = []

    for k in keys:
        raw = r.get(k)
        if not raw: continue
        p = raw.decode('utf-8', 'replace').split('$')
        if len(p) < 31: continue
        try:
            code = p[0]
            name = p[1]
            last = float(p[6])
            pre = float(p[30])
            turnover = float(p[29]) if len(p)>29 and p[29] else 0
            pct = (last-pre)/pre*100 if pre>0 else 0
            if turnover > 100 or turnover <= 0: continue
            if re.search(r'(ETF|增强|指数|基金|转债|\d+[增强]?)', name): continue
            if code.startswith(('SH5','SH0','SZ15','BJ')): continue
            if code.startswith('SH6') and turnover < 1: continue
            stocks.append({'code':code,'name':name,'last':last,'pct':pct,'turnover':turnover})
        except: pass

    if not stocks: return "⚠️ 无数据"

    now = datetime.now(CST)
    lines = [f"📊 实时监控 | {now.strftime('%H:%M:%S')}", ""]

    up = [s for s in stocks if s['pct'] >= 9.5 and 'ST' not in s['name']]
    if up:
        up.sort(key=lambda x: x['pct'], reverse=True)
        lines.append("🔥 涨停封板:")
        for s in up[:5]:
            lines.append(f" {s['name']}({s['code']}) {s['last']:.2f} {s['pct']:+.1f}% 换手{s['turnover']:.1f}%")
        lines.append("")

    big = [s for s in stocks if 5 <= s['pct'] < 9.5]
    if big:
        big.sort(key=lambda x: x['pct'], reverse=True)
        lines.append("📈 大涨 5~10%:")
        for s in big[:8]:
            lines.append(f" {s['name']}({s['code']}) {s['last']:.2f} {s['pct']:+.1f}% 换手{s['turnover']:.1f}%")
        lines.append("")

    down = sorted(stocks, key=lambda x: x['pct'])[:5]
    lines.append("❄️ 跌幅 TOP 5:")
    for s in down:
        lines.append(f" {s['name']}({s['code']}) {s['last']:.2f} {s['pct']:+.1f}% 换手{s['turnover']:.1f}%")
    lines.append("")

    hot = sorted([s for s in stocks if s['turnover'] > 10], key=lambda x: x['turnover'], reverse=True)[:5]
    if hot:
        lines.append("⚡ 高换手:")
        for s in hot:
            lines.append(f" {s['name']}({s['code']}) {s['last']:.2f} {s['pct']:+.1f}% 换手{s['turnover']:.1f}%")

    return "\n".join(lines)

def read_cache():
    """从本地缓存读板块数据"""
    if not os.path.exists(CACHE_FILE): return None
    try:
        mtime = os.path.getmtime(CACHE_FILE)
        if datetime.now().timestamp() - mtime > 600:  # 10分钟过期
            return None
        with open(CACHE_FILE) as f:
            content = f.read().strip()
        if not content: return None
        return content
    except: return None

def fetch_sectors():
    """从首尔 SSH 拉板块数据"""
    try:
        r = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=10', 'seoul', 'python3', '/tmp/sector_rank.py'],
            capture_output=True, text=True, timeout=25
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return r.stdout.strip()
    except: return None

def get_sector_rankings():
    """获取概念板块涨幅 TOP10（缓存优先）"""
    # 先尝试缓存
    data = read_cache()
    if data is None:
        data = fetch_sectors()
    
    if not data: return ""
    
    lines = ["", "📋 概念板块 TOP10（昨日）:"]
    for i, ln in enumerate(data.split('\n')[:10], 1):
        parts = ln.split('|')
        if len(parts) == 2:
            lines.append(f" {i:2d}. {parts[0][:12]} {parts[1]}%")
    return "\n".join(lines)

if __name__ == "__main__":
    if not is_trading():
        sys.exit(0)
    rankings = get_rankings()
    sectors = get_sector_rankings()
    print(rankings + sectors)
