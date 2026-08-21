# 2026-08-21 股票数据服务器三大问题修复

## 架构速览（重要，别再查错地方）
- **Mac L1 管道是宿主机进程（不是 Docker）**：launchd 托管
  - `com.stock.l1-receiver` (redis_data_receiver1.3.py) → **brew Redis (127.0.0.1:6379)** ← 真数据在这
  - `com.stock.writer` (writer_service_optimized.py) → Mac `~/go/data/intraday_snapshots_YYYY-MM-DD.duckdb`
  - `com.stock.snapshot` (snapshot_loop.sh → snapshot_sender.py) → SSH 推首尔
  - docker redis 容器是孤儿（未映射端口，数据停在 7/2），不在数据链路里！查 Redis 用 `redis-cli -p 6379`，别 docker exec
- **首尔**：8081 = 宿主机 `snapshot_api.py`（read_only）；写入方 = 宿主机 `snapshot_receiver_batch.py`（由 Mac com.stock.snapshot 每 ~30s 推一次）；seoul-data 容器只跑 cron，容器内 snapshot_receiver.py 已禁用路径

## 修复内容
1. **DuckDB 锁冲突（80% 失败）**：
   - 根因A：写入进程用 `executemany INSERT OR REPLACE`，8094 行要 **43~46 秒**（逐行绑定），锁窗口超长
   - 根因B：重复写入者 — `com.mia.snapshot-sync`(sync_snapshot.sh, docker exec 路径) 与 com.stock.snapshot 抢锁 → 已 unload + plist 改名 .disabled
   - 修复：receiver 改为 `read_json_auto(临时jsonl) + INSERT OR REPLACE SELECT`，锁窗口 **~0.5s**；先读 stdin 再开库
   - API 加锁冲突重试 5×0.5s；ranking 去 TEMP TABLE 改子查询
   - 重要：DuckDB read_only 连接若开持久连接会锁 WAL 挡住写入者！API 必须每次请求新建连接
2. **created_at 停在 7/9**：
   - 根因：DuckDB INSERT OR REPLACE 对已存在主键行 = UPDATE 语义，漏写 created_at 永远不刷新；且 root 属主 WAL（容器内 root 创建）导致 ubuntu 写 commit 失败（Permission denied）
   - 修复：INSERT 列清单显式加 `created_at=CURRENT_TIMESTAMP`；WAL chown 回 ubuntu
3. **Mac L1 管道**：实际健康（进程在跑、Redis/库都有当日数据），任务描述有误（查了孤儿 docker redis）。launchd KeepAlive 均已确认
4. **自愈**：首尔 cron 改为 `snapshot_health.sh`（30min：API 挂→重启；DB 锁死→杀卡死 receiver+重启容器+重启 API）

## 备份
- 首尔 `~/backup_20260821/`：snapshot_api.py、snapshot_receiver_batch.py(.orig/.staging)、snapshots.duckdb.bak、crontab.bak、容器内 receiver
- Mac `~/go/backup_launchd_20260821/com.mia.snapshot-sync.plist`

## 验证结果（2026-08-21 收盘）
- 压测 20/20 OK（写入循环进行中）；ranking 正常出 涨停榜
- /snapshot?code=000001 → created_at=2026-08-21 15:54（当天）
- 全表 6641 行 created_at 全部 = 8/21
- market_monitor 从 DB_LOCKED 恢复正常输出
