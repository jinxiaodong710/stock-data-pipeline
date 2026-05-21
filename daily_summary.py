#!/usr/bin/env python3
"""每日对话总结 — GPT-5.5 分析 + 微信推送"""
import duckdb, os, json, subprocess, requests
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DB = os.path.expanduser("~/.openclaw/mia_memory.duckdb")
API_KEY = "sk-137f54dedb17eb673f3967e069d5649ee004561c0e2dcbef08370c0fa0813447"
API_BASE = "https://api.schyler.top/v1"

def get_today_sessions():
    today = datetime.now(CST).strftime("%Y-%m-%d")
    c = duckdb.connect(DB, read_only=True)
    rows = c.execute("""
        SELECT summary, key_events, decisions FROM episodic_sessions 
        WHERE created_at >= ? ORDER BY created_at DESC
    """, [today]).fetchall()
    c.close()
    return rows

def get_today_conversations():
    today = datetime.now(CST).strftime("%Y-%m-%d")
    c = duckdb.connect(DB, read_only=True)
    rows = c.execute("""
        SELECT timestamp, topic, content, key_takeaway FROM conversations 
        WHERE timestamp >= ? ORDER BY timestamp
    """, [today]).fetchall()
    c.close()
    return rows

def main():
    now = datetime.now(CST)
    date = now.strftime("%m月%d日")
    
    sessions = get_today_sessions()
    convos = get_today_conversations()
    
    if not sessions and not convos:
        print("今日无对话")
        return
    
    # Build context for GPT
    items = []
    for s in sessions:
        items.append(f"会话: {s[0][:100]}")
        if s[1]:
            try: events = json.loads(s[1]) if isinstance(s[1], str) else s[1]; items.append(f"  事件: {', '.join(events[:5])}")
            except: pass
        if s[2]:
            try: decs = json.loads(s[2]) if isinstance(s[2], str) else s[2]; items.append(f"  决策: {', '.join(decs[:5])}")
            except: pass
    
    for c in convos:
        items.append(f"对话: [{c[1]}] {c[2][:80]}")
    
    context = "\n".join(items)
    
    # GPT-5.5 summary
    prompt = f"""你是米娅的每日总结助手。整理今天{date}的工作对话。

{context}

用简洁中文生成今日工作总结（100字内），包含：
1. 主要工作（3-5项）
2. 重要决策
3. 待办事项
格式用列表。"""
    
    try:
        r = requests.post(f"{API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": "gpt-5.5", "messages": [{"role":"user","content":prompt}], "max_tokens": 200},
            timeout=20)
        if r.status_code == 200:
            summary = r.json()["choices"][0]["message"]["content"].strip()
        else:
            summary = f"API错误:{r.status_code}"
    except Exception as e:
        summary = f"API异常:{e}"
    
    # Format final message
    report = f"""📋 米娅日报 | {date}

{summary}

📊 今日统计: {len(sessions)}段工作对话, {len(convos)}条记录
🕙 下期: 明日 22:00"""
    
    print(report)
    
    # Send via OpenClaw message if CLI available
    try:
        subprocess.run([
            "openclaw", "message", "send",
            "--channel", "openclaw-weixin",
            "--target", "o9cq800mXY4thLpF87zbMEtKIrsg@im.wechat",
            "--message", report
        ], timeout=10, capture_output=True)
    except:
        pass

if __name__ == "__main__":
    main()
