#!/usr/bin/env python3
"""Sync DuckDB → KNOWLEDGE.md (Hermes 三层记忆)"""
import duckdb, os, json

DB_PATH = os.path.expanduser("~/.openclaw/mia_memory.duckdb")
OUT_PATH = os.path.expanduser("~/.openclaw/workspace/KNOWLEDGE.md")

c = duckdb.connect(DB_PATH, read_only=True)
lines = ["## 米娅知识库（Hermes 三层记忆）\n"]

# Layer 1: Working
wc = c.execute("SELECT active_project, recent_topics, pending_items FROM working_context ORDER BY last_updated DESC LIMIT 1").fetchone()
if wc:
    lines.append("### 🧠 工作记忆")
    lines.append(f"- 当前项目: {wc[0]}")
    lines.append(f"- 最近话题: {wc[1]}")
    lines.append(f"- 待办: {wc[2]}")
    lines.append("")

# Layer 3: Semantic (core facts)
lines.append("### 🔗 语义记忆")
rows = c.execute("SELECT name, type, description FROM semantic_entities ORDER BY importance DESC, type, name").fetchall()
for name, etype, desc in rows:
    display = desc[:60] if etype == 'api_key' else desc
    lines.append(f"- **[{etype}] {name}**: {display}")

rel_rows = c.execute("SELECT subject, predicate, object, detail FROM semantic_relations ORDER BY created_at DESC LIMIT 15").fetchall()
if rel_rows:
    lines.append("")
    for s, p, o, d in rel_rows:
        lines.append(f"- {s} → {p} → {o} ({d})" if d else f"- {s} → {p} → {o}")
lines.append("")

# Layer 2: Episodic
lines.append("### 📖 情节记忆")
ep_rows = c.execute("SELECT started_at, summary, key_events, decisions FROM episodic_sessions ORDER BY created_at DESC LIMIT 10").fetchall()
if ep_rows:
    for ts, summary, events, decisions in ep_rows:
        lines.append(f"- **{str(ts)[:10]}**: {summary}")
        if decisions:
            try:
                decs = json.loads(decisions) if isinstance(decisions, str) else decisions
                for d in decs[:3]:
                    lines.append(f"  → 决策: {d}")
            except:
                pass
else:
    lines.append("  (暂无)")

c.close()
with open(OUT_PATH, "w") as f:
    f.write("\n".join(lines))

print(f"✅ KNOWLEDGE.md updated")
