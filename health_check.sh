#!/bin/bash
# 小五每日健康复核 - Mac 端
# launchd 守护，每30分钟跑一次，有问题推微信

# === 1. L1 进程 ===
REC=$(ps aux | grep 'redis_data_receiver1.3' | grep -v grep | wc -l)
WRI=$(ps aux | grep 'writer_service_optimized' | grep -v grep | wc -l)
SNAP=$(ps aux | grep 'snapshot_loop' | grep -v grep | wc -l)
[ "$REC" -ge 1 ] && R="✅" || R="❌"
[ "$WRI" -ge 1 ] && W="✅" || W="❌"
[ "$SNAP" -ge 1 ] && S="✅" || S="❌"

# === 2. 首尔快照新鲜度 ===
SN=$(ssh -o ConnectTimeout=5 seoul 'python3 -c "
import duckdb,time
for i in range(3):
 try: con=duckdb.connect(\"/home/ubuntu/go/data/snapshots.duckdb\",read_only=True); break
 except: time.sleep(2)
r=con.execute(\"SELECT max(created_at) FROM snapshots\").fetchone()[0]
print(r.isoformat())
con.close()
"' 2>/dev/null)
SN_OK="❌"
if [ -n "$SN" ]; then
    SN_TS=$(date -j -f "%Y-%m-%dT%H:%M:%S" "${SN%.*}" "+%s" 2>/dev/null)
    NOW_TS=$(TZ=Asia/Shanghai date +%s)
    DELAY=$((NOW_TS - SN_TS))
    [ "$DELAY" -lt 90 ] && SN_OK="✅" || SN_OK="⚠️${DELAY}s"
fi

# === 3. 价格交叉验证（301596 瑞迪智驱）===
WEB=$(curl -x http://127.0.0.1:7897 -s 'https://push2.eastmoney.com/api/qt/stock/get?secid=0.301596&fields=f43,f170' 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin).get('data',{}); print(f\"{d.get('f43',0)/100:.2f}|{d.get('f170',0)/100:.2f}\")" 2>/dev/null)
SNAP=$(ssh -o ConnectTimeout=3 seoul 'python3 -c "
import duckdb,time
for i in range(3):
 try: con=duckdb.connect(\"/home/ubuntu/go/data/snapshots.duckdb\",read_only=True); break
 except: time.sleep(2)
r=con.execute(\"SELECT last,pct_change FROM snapshots WHERE code='\''SZ301596'\'' ORDER BY created_at DESC LIMIT 1\").fetchone()
print(f\"{r[0]:.2f}|{r[1]:.2f}\") if r else print(\"NA\")
con.close()
"' 2>/dev/null)
PV_OK="✅"
if [ -n "$WEB" ] && [ -n "$SNAP" ]; then
    WP=$(echo "$WEB" | cut -d'|' -f1)
    SP=$(echo "$SNAP" | cut -d'|' -f1)
    DIFF=$(python3 -c "print(abs($WP - $SP))")
    [ "$(python3 -c "print(1 if $DIFF > 0.5 else 0)")" = "1" ] && PV_OK="❌${DIFF}"
fi

# === 汇总 ===
TZ='Asia/Shanghai' date "+🩺 %H:%M"
echo "  $R receiver $W writer $S snapshot | 快照$SN_OK | 验价$PV_OK"

# 有问题才报警
if [ "$R" = "❌" ] || [ "$W" = "❌" ] || [ "$S" = "❌" ] || [ "$SN_OK" != "✅" ] || [ "$PV_OK" != "✅" ]; then
    /opt/homebrew/bin/openclaw message send \
      --channel openclaw-weixin \
      --target 'o9cq800mXY4thLpF87zbMEtKIrsg@im.wechat' \
      --message "⚠️ 健康报警 | $R receiver $W writer $S snapshot | 快照$SN_OK | 验价$PV_OK" 2>/dev/null
fi
