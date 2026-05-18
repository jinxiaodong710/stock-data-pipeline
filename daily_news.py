#!/usr/bin/env python3
"""每日新闻早报 - 多源抓取"""
import urllib.request, re, json
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
today = datetime.now(CST).strftime('%Y年%m月%d日')

headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

news = []

# 1. 金十数据
try:
    req = urllib.request.Request('https://www.jin10.com/', headers=headers)
    html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='replace')
    titles = re.findall(r'title=["\']([^"\']{10,120})["\']', html)
    for t in titles[:8]:
        if any(k in t for k in ['指数','行情','基金']):
            continue
        news.append(('金十', t))
except Exception as e:
    news.append(('系统', f'金十数据获取失败: {e}'))

# 2. 东方财富快讯 (RSS)
try:
    req = urllib.request.Request('https://finance.eastmoney.com/a/czqyw.html', headers=headers)
    html = urllib.request.urlopen(req, timeout=10).read().decode('gbk', errors='replace')
    # 尝试提取标题
    etitles = re.findall(r'<a[^>]*href="([^"]*)"[^>]*title="([^"]{8,80})"', html)
    for url, t in etitles[:5]:
        if '/news/' in url and not any(k in t for k in ['广告','推广']):
            news.append(('东财', t))
except Exception as e:
    pass

# 3. 降级：用已知头条
if len(news) < 5:
    news.append(('综合', 'A股今日收盘数据可查看 stock_prices 表最新日期'))
    news.append(('综合', '关注沪深两市成交量及北向资金动向'))
    news.append(('综合', '建议关注近期政策面及行业轮动热点'))

# 输出
print(f"📰 每日财经早报 — {today}")
print("=" * 40)

# 去重
seen = set()
count = 0
for source, title in news:
    key = title[:20]
    if key in seen:
        continue
    seen.add(key)
    print(f"• [{source}] {title}")
    count += 1
    if count >= 12:
        break

print("=" * 40)
print("💬 市场简评：隔夜关注美股走势及人民币汇率，今日注意A股量能变化。")
print(f"⏰ 自动推送 | {today}")
