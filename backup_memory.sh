#!/bin/bash
# 记忆文件备份 + 滚动清理（保留最近2天）
# 中午12点、凌晨0点执行（北京时间）
set -e

WS="$HOME/.openclaw/workspace"
BACKUP_DIR="$HOME/go/memory_backup"
LOG="/tmp/memory_backup.log"

echo "$(date '+%Y-%m-%d %H:%M:%S') 记忆备份开始" >> "$LOG"

# 1. 同步记忆文件到备份目录
mkdir -p "$BACKUP_DIR"
cp "$WS/MEMORY.md" "$BACKUP_DIR/MEMORY.md" 2>/dev/null
cp "$WS/MEMORY.md" "$BACKUP_DIR/MEMORY.md" 2>/dev/null
rsync -a "$WS/memory/" "$BACKUP_DIR/memory/" 2>/dev/null

# 2. Git 提交
cd "$HOME/go"
git add memory_backup/ 2>/dev/null
if git diff --cached --quiet 2>/dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') 无变更，跳过提交" >> "$LOG"
else
    git commit -m "memory: $(date '+%Y-%m-%d %H:%M') 自动备份" >> "$LOG" 2>&1
    git push origin master >> "$LOG" 2>&1
    echo "$(date '+%Y-%m-%d %H:%M:%S') 已提交并推送" >> "$LOG"
fi

# 3. 滚动删除：只保留最近2天的 memory/YYYY-MM-DD.md
CUTOFF=$(date -v-2d '+%Y-%m-%d' 2>/dev/null || date -d '2 days ago' '+%Y-%m-%d')
for f in "$WS/memory"/20*.md; do
    [ -f "$f" ] || continue
    base=$(basename "$f" .md)
    if [[ "$base" < "$CUTOFF" ]]; then
        rm "$f"
        echo "  删除旧记忆: $base" >> "$LOG"
    fi
done

echo "$(date '+%Y-%m-%d %H:%M:%S') 备份完成" >> "$LOG"
