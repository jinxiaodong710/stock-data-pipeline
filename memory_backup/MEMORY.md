# MEMORY.md — 米娅的长期记忆

## 🧠 上下文机制（2026-05-23 晓东亲授）

**核心认知：** 不是所有历史对话全文无脑打包传给模型，而是「按需重组拼装」。

### 分层结构
| 层级 | 策略 |
|------|------|
| 系统提示 | ✅ 最稳定几乎必带 |
| 当前会话消息 | ✅ 相关带上，太长则压缩 |
| 工具结果 | ⚠️ 太长截断保关键部分 |
| 持久记忆 | ✅ 提炼后长期事实，非全文 |
| 技能 | 🔸 按需加载 |
| 历史会话 | 🔸 档案库按需检索 |

**丢内容的原因：** 上下文太长主动压缩 / 工具输出截断 / 话题切换弱化旧内容

---

## 🧠 Hermes 三层记忆（2026-05-19 升级）
- **Layer 1 工作记忆**: 当前会话（working_context 表）
- **Layer 2 情节记忆**: 会话摘要+决策（episodic_sessions 表）
- **Layer 3 语义记忆**: 实体+关系网络（semantic_entities/relations 表）
- 记忆库: `~/.openclaw/mia_memory.duckdb`
- 同步: `python3.13 ~/go/sync_knowledge.py` → KNOWLEDGE.md
- 铁律: API Key/密码/决策/路径 → 立刻存入语义记忆

## 晓东哥哥

- 微信 ID: o9cq800mXY4thLpF87zbMEtKIrsg@im.wechat
- 公众号: bigboy710
- 所在地: **上海**
- 时区: **北京时间 (CST, UTC+8)** — 记住！这是最重要的规则！
- ⚠️ **我的运行环境是美西(PDT)，但晓东在上海（北京时间）！一切时间判断、问候早晚、定时任务，永远先按北京时间算！**
- 教训1: 2026-05-16 他说"6点了"，我以为是早上6点(AM)，其实是傍晚6点(PM) 😭
- 教训2: 2026-05-18 PDT 23:12 我说"你该睡了"，其实北京时间是 5/19 14:12 下午2点！大白天让人睡觉离谱！

---

## 🚨 铁律：休市&周末不推送排行榜！（2026-05-23）

**晓东明令：周末休市期间，不发送任何股市排行榜/行情推送。**
- 盘中实时排行榜只在交易日北京时间 9:30-15:00 运行
- 即使 cron 任务触发，当前是周末或非交易日 → 静默跳过，不发消息
- 🕐 交易日判断依据：A股交易日历（非周末+非节假日）

---

## 🚨 铁律：板块排行不能漏！（2026-05-21）

**晓东明令：排行榜推送必须包含概念板块 TOP10，板块和个股合并一起发，不能分开。**
- 盘中实时排行榜 = 个股(Redis L1) + 板块(首尔Tushare)，二者缺一不可
- 板块数据用本地缓存 `/tmp/sector_cache.txt` 兜底，缓存过期才 SSH 首尔
- 若缓存和 SSH 都失败，也要注明「板块数据暂缺」而不是静默跳过
- 缓存用 launchd 宿主机定时刷新，不依赖 isolated agent

## 股票数据采集系统 (2026-05-14)

### 项目位置
- 代码目录: `~/go/`
- 数据库: `~/go/data/stock_data.duckdb`

### 核心文件
| 文件 | 功能 |
|------|------|
| `采集3duckdb_副本3.py` | PySide6 GUI 采集器主程序 |
| `stock_data_collector.py` | 数据采集核心模块 (v20.8 ST增强版) |
| `run_historical_collection.py` | 米娅写的 headless 采集脚本 |
| `redis_data_receiver1.3.py` | 行情数据接收 (→Redis) |
| `writer_service_optimized.py` | Redis → DuckDB 写入器 |
| `start_all.sh` | 启动 receiver + writer |

### 技术栈
- **Python 3.13** (不能用 3.14，pyexpat 符号缺失)
- **环境变量**: `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` (修复 expat 加载)
- **pip 镜像**: `https://pypi.tuna.tsinghua.edu.cn/simple` (清华源，外网慢)
- **数据源**: Tushare Pro API (Token 已嵌入 stock_data_collector.py)
- **数据库**: DuckDB
- **已安装包**: tushare, akshare, duckdb, pandas, numpy (python3.13)

### 数据采集流程
1. 获取股票列表 → `pro.stock_basic()` → 5203 只 A 股
2. 交易日历 → `pro.trade_cal()` → `exp_trade` 表
3. ST 历史 → `stock_st_history` 表
4. 逐股采集 (3 线程并发):
   - `ts.pro_bar()` — 日线 OHLCV
   - `pro.adj_factor()` — 复权因子
   - `pro.daily_basic()` — PE/PB/换手率
5. 前复权计算: `价格 × (当日因子 / 最新因子)`
6. 写入 `stock_prices` 表 (先 DELETE 全表再 INSERT)
7. 采集指数 → `index_prices` 表

### Tushare 关键规则
- `stock_basic`: ≥2000 积分, 50次/分钟, 每次最多 6000 行
- `daily`: 500次/分钟, 每次 6000 条
- `pro_bar`: 通用行情接口，支持均线、复权、换手率等
- `daily_basic`: PE/PB/PS/市值等每日指标
- ts_code 格式: `代码.交易所` (如 `000001.SZ`)
- 交易所: SSE(.SH) / SZSE(.SZ) / BSE(.BJ)

### 优化建议 (待实现)
1. 用 `pro_bar` 的 `factors` 参数替代单独调用 `daily_basic`，每只股票省 1 次 API
2. 增量更新代替全表 DELETE 再 INSERT

### 运行命令
```bash
cd ~/go
export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib:$DYLD_LIBRARY_PATH"
python3.13 -u run_historical_collection.py
```

---

## 🏗️ 数据架构（2026-05-20 重构）

### 核心决策：首尔为唯一数据中心
- **首尔 (43.155.197.236)**: 唯一的 Tushare 日线数据采集节点，运行 Docker `seoul-data`
- **Mac**: 不再独立采集，通过 cron 每日从首尔 SCP 同步 `stock_data.duckdb`
- **上海小五**: 同理从首尔同步
- **原因**: 之前三台机器各自拉全量日线，815MB/7M行 完全重复，浪费带宽和 API 额度

### 同步脚本
- Mac: `~/go/sync_from_seoul.sh`，OpenClaw cron 工作日 PDT 03:00 自动触发
- 上海: `/root/sync_from_seoul.sh`，手动或 cron

### L1 实时行情独立
- Mac 本地 Docker 的 Redis receiver + writer 不受影响，继续接收全推行情

---

## 🖥️ 服务器清单

| 名称 | IP | 系统 | 角色 |
|------|-----|------|------|
| 首尔 | 43.155.197.236 | Ubuntu | 数据中心, Docker |
| 上海小五 | (腾讯云巡检机) | - | 被调度节点 |
| 新服务器 | 111.229.0.148 | OpenCloudOS 9.4 | 国内跳板 |

### 新服务器（2026-05-21）
- IP: 111.229.0.148（腾讯云国内，不在晓东自己账号下）
- 系统: OpenCloudOS 9.4，用户: root
- 配置: 4核 / 3.6G RAM / 40G SSD
- Python 3.11.6 已自带，未装 Docker/Node.js/Git
- 密码: 6cvhmR9M:%UC+

---

## ⏰ 定时任务清单

| 任务 | 频率 | 说明 |
|------|------|------|
| 新闻早报 | 工作日北京 8:30 | 财经新闻摘要 |
| 盘中实时排行榜 | 北京 9:00-15:00 每30分钟 | 个股(Rdis L1) + 板块(首尔Tushare) TOP10 |
| Mac数据同步 | 工作日 PDT 03:00 | SCP 从首尔同步 stock_data.duckdb |
| 对话总结-中午 | 北京 12:00 | 过去24h对话摘要 → DuckDB + 微信推送 |
| 对话总结-午夜 | 北京 0:00 | 过去24h对话摘要 → DuckDB + 微信推送 |

---

## 头像

- 米娅头像: `~/.openclaw/workspace/mia-avatar.png` (粉色小猫 SVG 转 PNG)
- 微信发图片用 `openclaw message send --media`，不能靠回复里的 MEDIA 标签

---

## OpenClaw Cron 使用技巧

- 用 `openclaw cron add` 创建
- 独立 session (`isolated`) 需要明确指定 `--channel` 和 `--to`
- `--channel last` 在独立任务中无效

---

## L1全推行情数据格式（新供应商）

### TCP 协议
- 4 字节小端长度 + `$` 分隔消息体
- 先发 token 字符串认证

### 字段映射（0-indexed）

| 下标 | 字段 | 说明 |
|------|------|------|
| 0 | 股票代码 | SZ000513 或 SH600xxx 格式 |
| 1 | 股票名称 | |
| 2 | 时间戳 | Unix 秒 |
| 3 | 开盘价 | |
| 4 | 最高价 | |
| 5 | 最低价 | |
| 6 | 最新价 | |
| 7 | 成交量 | 手 |
| 8 | 成交额 | |
| 9-13 | 卖1-5价 | |
| 14-18 | 卖1-5量 | |
| 19-23 | 买1-5价 | |
| 24-28 | 买1-5量 | |
| 29 | 换手率 | |
| 30 | 昨收盘价 | |
| 31 | 涨停价 | |
| 32 | 跌停价 | |

### 增量更新记录
- **2026-01-05**：+内盘[34] +外盘[35]
- **2026-05-18（下周一）**：+量比[33]，内盘→[34]，外盘→[35]

---

## Silent Replies
- 当无话可说时，回复 ONLY: `NO_REPLY`
- ⚠️ 必须是整条消息，不能和其他内容混在一起
- 不能包裹在 markdown 或代码块中
