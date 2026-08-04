# Outbound 队列积压记录（2026-08-04 12:58 修复子代理）

## 位置
- SQLite: `~/.openclaw/state/openclaw.sqlite`
- 表: `delivery_queue_entries` (queue_name='outbound')
- schema 关键列: queue_name, id, status, entry_kind, session_key, channel, target, account_id, retry_count, last_attempt_at, last_error, entry_json, enqueued_at, updated_at, failed_at

## 积压统计（修复时快照）
| status | 数量 | 最早 enqueued | 最新 enqueued | 最新 last_attempt |
|--------|------|--------------|--------------|------------------|
| failed | 262  | 2026-05-21 06:01 | 2026-08-01 10:58 | 2026-08-01 10:58 |
| pending| 37   | 2026-08-03 09:07 | 2026-08-04 10:00 | 2026-08-04 10:00:07 |

## 失败原因分布（failed 262 条）
- 155 条: `sendMessage ret=-2 errmsg=prepare failed`（微信通道间歇性故障）
- 97 条: `fetch failed | getaddrinfo ENOTFOUND ilinkai.weixin.qq.com`（DNS 解析失败，通道端点不可达）
- 7 条: `fetch failed`（网络错误）
- 1 条: weixin not configured（早期未登录）
- 1 条: uploadFileAttachmentToWeixin 无上传 URL
- 1 条: ENOENT 媒体文件缺失（`~/.openclaw/workspace/~/.openclaw/media/browser/...jpg` 路径错误）

## 内容特征
- 全部发往同一目标: 晓东微信 `o9cq800mXY4thLpF87zbMEtKIrsg@im.wechat`，account `a24374162acb-im-bot`
- 主要是 cron 定时任务的「📊 实时监控 | HH:MM」A股涨跌推送（涨停封板/大涨名单）+ cron failure 通知
- pending 37 条含 cron:*:failure 通知约 20+ 条、agent 消息约 9 条、cron run 消息若干

## 处理决定：不硬删（安全边界）
- gateway 正在运行并持有该 SQLite（WAL 模式），直接删行有状态不一致风险
- pending 37 条是最近（8/3-8/4）未送达消息，通道恢复后 gateway 仍会重试（最新尝试 8/4 10:00:07），不应删
- failed 262 条为终态垃圾数据，但无官方 CLI 清理命令（`openclaw debug` 不存在，plugins/registry 命令均不涉及 delivery queue）
- 建议后续观察：通道稳定后 pending 应自动清空；failed 若持续增长需排查 cron 推送链路（ilinkai.weixin.qq.com DNS）
