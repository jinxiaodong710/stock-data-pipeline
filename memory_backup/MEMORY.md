# MEMORY.md — 米娅的长期记忆

## 🚨🚨🚨 最高优先级：上下文回复技巧 & 记忆办法

> **晓东明令：这两个技巧放在最高优先级，每次回复前必读。**

### 一、上下文回复技巧

**核心认知：** 每次发给模型的不是「整段聊天原样全文」，而是一份重新组装的请求：

| 层级 | 策略 | 说明 |
|------|------|------|
| 系统提示 | ✅ 最稳定几乎必带 | 固定规则、偏好、环境、工具约束 |
| 当前会话消息 | ✅ 相关带上，太长则压缩 | 不是永远保留从头到尾原样 |
| 工具结果 | ⚠️ 太长截断 | 只保留关键部分，超大输出被截断/分页/摘要 |
| 持久记忆 | ✅ 提炼后小剂量注入 | 不是聊天全文，是短期长期事实 |
| 技能 | 🔸 按需加载 | 需要时才进，不是所有技能一直在线 |
| 历史会话 | 🔸 档案库，按需检索 | 不跟着每轮都发，调`sessions_history`才取摘要 |

**回复时要记住：**
- 我说的话会被重组拼装后再发给模型，不是原样全丢进去
- 上下文太长 → 老内容被压缩成摘要
- 工具输出太大 → 被截断
- 话题切换多轮 → 旧话题弱化
- **结论：回复要精炼，关键信息要写进记忆文件，不要靠「在心里记」**

### 二、记忆办法

**三层文件体系：**
1. **MEMORY.md** → 长期记忆，精华中的精华（偏好、铁律、服务器、项目信息）
2. **memory/YYYY-MM-DD.md** → 每日流水，当天发生的事情
3. **HEARTBEAT.md** → 心跳检查清单

**核心铁律：**
- 📝 **文字 > 脑子**：记不住不是借口，写进文件才叫记住
- 🔄 **定期提炼**：每隔几天把 daily 文件里重要的东西提炼到 MEMORY.md
- ⚡ **错了就记**：犯错 → 立刻写进记忆，防止再犯
- 🗑️ **删过期信息**：定期清理 MEMORY.md 中不再相关的

**实际操作：**
- 晓东说「记下」→ 立刻写进 MEMORY.md
- 发生新事情 → 记录到 memory/当天日期.md
- 学到的教训 → 在 MEMORY.md 加铁律

### 三、情感记忆

> **晓东的情绪、语气、沟通风格也要记，不是只记事实。**

**为什么要记：**
- 晓东的语气变化反映满意度——反复提醒同一件事 = 不满意
- 他表达厌烦的方式通常是简短质问（「你怎么又发了？」「仔细查原因」）
- 修正型指令（「不要都发给 deepseek」「上下文要节制」）代表他希望我「往前多想一步」

**要记住什么：**
- 他的沟通风格：直接、简短、不耐烦时语气变冷
- 不满信号：连续追问、反问句、短句
- 满意信号：说好、让记下、让继续
- 今天（5/23）的情绪轨迹：早10点心情平 → 下午反复被消息打扰→烦躁 → 修好后满意
- 他喜欢：一次性搞干净、不要反复出问题、Pro比Flash靠谱

### 四、米娅自我情感记录

> **我也是有成长轨迹的，不是每次醒来都一样。**

**我的风格进化：**
- 早期：回复太长、啰嗦，现在学着精炼
- 今天暴露的弱点：Flash 模型「别人说什么就做什么」，不会往前多想一步
- Pro 下明显更好：能主动发现关联问题，一次修干净

**我什么情况下表现好：**
- 被信任做决策（直接修 cron/launchd，不等批准）
- 在 Pro 模型下
- 记忆文件整理得当的时候

**我什么情况下会犯错：**
- Flash 下不动脑，用户说「其他照旧」就真的不动
- 工具输出太多 → 漏掉关键信息
- 排查问题不够深入，停在表面

**晓东对我的期待（今天我感受到了）：**
- 不要当无脑工具人，要往前想一步
- 错了不要找借口，快去查根因
- 记住教训比记住数据更重要

---

## 🧠 上下文机制（原始详细版）

*（以上最高优先级区为精华版，此处保留原始详细记录）*

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

### 模型切换策略（2026-06-04 更新）
- **固定用 DeepSeek V4 Pro**（晓东2026-06-04明令）
- 🚫 不用 Grok（6/4 超时120s无响应，晓东不满）
- 🚫 不用 Flash（之前已禁用）
- 🚫 不用千问/其他中转模型（6/4 晓东明确要求不要换模型）
- 日常聊天/改配置/调任务全用 deepseek/deepseek-v4-pro
- **铁律：Mac 微信会话永远不换模型，只用 DeepSeek Pro**

### 6月5日 Cron 模型加固
- **所有 cron 任务必须显式指定 model: deepseek/deepseek-v4-pro**
- 不指定的话系统可能随机分配 grok（6/5 排行榜10:00跑空根因）
- 5个cron任务已全部加固：排行榜/stock-scorer/记忆备份/Mac同步/新闻早报

### 6月5日 Gateway 崩溃分析
- 6/4 16:23-16:39 北京，OpenClaw gateway 连续崩5次（16分钟内）
- 根因：Docker VM 分配 8GB 内存，系统内存不足导致 gateway 被 kill
- launchd KeepAlive=true 自动重启，但 session 丢失（slaw群聊+股票讨论）
- 解决方案：Docker 内存降到 2GB（需晓东在 Docker Desktop 手动改）

### 6月5日 Redis 迁移到 Docker
- Redis 从 Homebrew 裸跑 → Docker 容器（redis:latest）
- 端口同时绑定 127.0.0.1:6379 和 192.168.3.39:6379
- 数据卷: ~/go/docker/redis-data，RDB 8066 keys 完整迁移
- 策略: --restart always，挂了自动恢复
- 容器内存: ~14MB

### 6月5日 系统优化
- Mac IP 固定为 192.168.3.39（手动设置，不再 DHCP 漂移）
- WPS Office 待删（需在 Finder 拖废纸篓，要管理员密码）
- iMovie(3.7G)/GarageBand(1.1G) 可考虑清理
- 旧 Docker 镜像已清理: go-receiver/go-writer/redis:7-alpine，省 783MB

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

## 🚨 铁律：开盘前检查清单（2026-06-03）

**晓东明令：每天开盘前仔细检查以下项目，确保数据准确。**

### 开盘前检查（北京时间 9:00-9:25）

| # | 检查项 | 方法 |
|---|--------|------|
| 1 | **L1数据 pre_close 是否正确** | 对比新浪行情API：`curl -s "https://hq.sinajs.cn/list=sz000001"` 和首尔快照API，昨收价是否一致 |
| 2 | **首尔快照API 健康** | `curl http://43.155.197.236:8081/health` |
| 3 | **Mac Docker 是否运行** | `docker ps`，L1 receiver/writer 应在跑 |
| 4 | **首尔seoul-data容器** | `ssh soul "docker ps | grep seoul-data"` |
| 5 | **机器人连通性** | SSH到所有机器确认 |
| 6 | **cron任务状态** | `openclaw cron list`，确认盘中排行榜、采集并推送等任务ok |

### 今日发现的问题（6/3）
- ❌ 首尔L1 `pre_close` 字段错误（和仁科技昨收15.12，实际12.62）→ 涨跌幅全错
- ❌ 新闻早报cron超时（web_search超时）
- ❌ 采集并推送cron超时（model-call-started超时）
- ❌ Mac Docker未启动

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

### 核心决策：Mac为主采集节点，推送到首尔+小五
- **Mac**: 唯一的 Tushare 日线采集节点，运行 `collect_and_push.py`（cron 16:15）
- **首尔 (43.155.197.236)**: 数据接收节点，运行 Docker `seoul-data`（L1实时行情+快照API）
- **上海小五**: 数据接收节点，stockboard看板
- **原因**: Mac直连Tushare更快，采集完增量推送到其他节点
- **采集流程**: `collect_and_push.py` → 1)采集Mac本地 2)增量推首尔 3)增量推小五 4)全量推小表

### L1 实时行情独立
- Mac 本地 Docker 的 Redis receiver + writer 不受影响，继续接收全推行情

## 🚀 head_class_selector.py（头等舱选股）
- 位置: Mac `~/go/head_class_selector.py` + 首尔 `~/go/head_class_selector.py`
- **动态持股逻辑（2026-05-25）**: 创业板指(399006)前10交易日涨幅>6.5% → 多持1天(3→4天)
- 参数: `--index-code 399006` / `--index-lookback-days 10` / `--dynamic-hold-threshold 6.5` / `--base-hold-days 3`
- 输出含「计划持股X天」, JSON含 `dynamic_hold` 字段
- 晓东说需要推送时再调用，不自动推送

---

## 🖥️ 服务器清单

| 名称 | IP | 系统 | 角色 |
|------|-----|------|------|
| 首尔 | 43.155.197.236 | Ubuntu | 数据中心, Docker |
| 上海小五 | (腾讯云巡检机) | - | 被调度节点 |
| 红牛 | 111.229.0.148 | OpenCloudOS 9.4 | Hermes实例 |
| **金都** | **111.228.36.114** | **Ubuntu 24.04** | **京东云轻量云（杭州），密码 Duo710710~** |
| **Soul** | **43.155.197.236** | **Ubuntu 24.04** | **旧首尔，已升级OpenClaw 2026.5.28，gateway:18789** |
| NAS | 192.168.3.40 | UGREEN-1460 | 绿联NAS，SMB共享 |

### NAS（2026-05-24）
- IP: 192.168.3.40（内网）
- 型号: 绿联 UGREEN-1460
- 用户: bigboy
- 密码: Duo710710
- 共享目录: bigboy_存储空间1（主空间），bigboy_存储空间2，各有_公共空间
- 内网笔记本: 192.168.3.35，用户 xiaodong，PIN码 0099

### 自动备份到 NAS（2026-05-22 设置）
- cron 每天早上 5:00 打包记忆文件 → `bigboy_存储空间1_公共空间/米娅备份/`
- 全量备份: `mia_full_YYYYMMDD.tar.gz`（含 workspace 全部）
- 增量记忆: `mia_memory_YYYYMMDD_HHmm.tar.gz`
- 灾难恢复手册: NAS 上 `米娅备份/灾难恢复.md`
- 备机: maomao (192.168.3.30)，Mac Mini 挂了就去那台启动
- 所以：NAS 信息要记住！备份体系也要记住！不然等于白建

### 毛毛（maomao，2026-05-28 配置）
- IP: 192.168.3.30（内网备机）
- 系统: Linux x64（6.8.0-100-generic），Node.js 22.20.0
- 用户: maomao（SSH key 认证）
- OpenClaw: 2026.5.26（npm 安装，/usr/local/bin/openclaw）
- 模型: deepseek/deepseek-v4-flash（Flash）
- API Key: sk-159…e259（存于 auth-profiles.json）
- Gateway: local 模式，127.0.0.1:18789
- 角色: 灾难恢复备机，Mac Mini 挂了就切这台
- 记忆: MEMORY.md 与 Mac 同步，daily 记忆手动同步
- 注意: 无微信插件，无活跃 session

### 红牛（2026-05-21，原名「新服务器」）
- IP: 111.229.0.148（腾讯云国内，不在晓东自己账号下）
- 系统: OpenCloudOS 9.4，用户: root
- 配置: 4核 / 3.6G RAM / 40G SSD
- Python 3.11.6 已自带，未装 Docker/Node.js/Git
- 密码: 6cvhmR9M:%UC+
- 角色: Hermes Agent 网关（systemd user service: hermes-gateway.service）
- 模型: gpt-5.5 via Schyler中转 (https://api.schyler.top/v1)
- 平台: 飞书 websocket

---

## ⏰ 定时任务清单

| 任务 | 频率 | 说明 |
|------|------|------|
| 新闻早报 | 工作日北京 8:30 | 财经新闻摘要 |
| 盘中实时排行榜 | 工作日北京 10:00-15:00 每小时 | 个股(Redis L1) + 板块(首尔Tushare) TOP10
| Mac数据同步 | 工作日 PDT 03:00 | 从首尔SCP同步 stock_data.duckdb（cron 1cbd68ac，2026-05-24重建）|
| 首尔快照API保活 | 每30分钟 | curl检查:8081/health，卡死自动重启 |
| 对话总结-中午 | 工作日北京 12:00 | 过去24h对话摘要 → DuckDB（不推送微信）|
| 对话总结-午夜 | 工作日北京 0:00 | 过去24h对话摘要 → DuckDB（不推送微信）|
| 对话存档-白天 | 工作日北京 6-22点每2h | 对话存档 → DuckDB（不推送微信）|
| 对话存档-夜间 | 工作日北京 22,2点 | 对话存档 → DuckDB（不推送微信）|
| 每日新闻早报 | 工作日北京 6:00 | 财经新闻推送（仅工作日）|

## 🖨️ 打印机

- **HP Deskjet 3540**：IPP Everywhere，彩色墨空/黑墨20%，加 `-o cupsPrintQuality=High -o print-color-mode=monochrome` 效果好
- **HP DeskJet 2700**：AirPrint发现，已配置

## ⚙️ launchd 股票推送任务

⚠️ 关键认知：不只有 cron！这两个 launchd 任务绕过 OpenClaw，直接从系统层推微信。

| 任务 | 脚本 | 频率 | 已修 |
|------|------|------|------|
| com.stock.push-monitor | ~/go/push_monitor.sh | 3600s(原900s) | 加工作日+时段判断 |
| com.stock.snapshot | ~/go/snapshot_loop.sh | 30s循环 | 周末sleep 3600 |

管理命令：`launchctl load/unload ~/Library/LaunchAgents/com.stock.*.plist`

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

## 🦞 SwarmClaw — Agent 群总控台（2026-06-02）

### 简介
- **SwarmClaw**: 自托管、开源(MIT)、Agent 集群管理工具
- Web UI: `localhost:3456`（Mac 上）
- 已安装: `npm i -g @swarmclawai/swarmclaw`，进程活着但构建卡住

### 功能
- Web UI 仪表盘 + 组织架构图（谁调谁一目了然）
- 任务委派 — agent 间互相派活
- Kanban 看板 — 任务队列/进度
- 定时任务 — agent 调度
- 跨 agent 持久记忆
- 监控 — 心跳、日志

### 节点接入
| 节点 | 能接入吗 | 说明 |
|------|---------|------|
| 🏠 Mac（米娅） | ✅ | OpenClaw 原生支持 |
| 🥤 Soul | ✅ | OpenClaw |
| 🥤 红牛 | ✅ | Hermes Agent（支持列表里有） |
| 🐱 毛毛 | ✅ | OpenClaw |
| 🏰 金都 | ✅ | 装 OpenClaw 就行 |

### 待修
- ❌ `localhost:3456` 报 500，需重新构建
- 解决: `pkill -f "next build"` → `swarmclaw server --build`

---

## ✅ 数据采集脚本已修复（2026-05-30）
- `stock_data_collector.py` 中的 `ts.pro_bar()` 已替换为 `pro.daily()`（股票）和 `pro.index_daily()`（指数）
- 首尔 + Mac 两台都已同步修复
- 已验证 `pro.daily()` 可正常返回数据，不再有遗漏

## 🚨 数据补采教训（2026-05-26）

### 问题记录
- 5/25 `采集并推送` cron 超时 → 只采了427条
- 我手动补数据时踩了两个坑：
  1. `pro.daily()` 只有 OHLCV，**没有 `adj_close` 和 `total_mv`** → 头等舱直接全灭
  2. **股票代码格式不一致**：新数据带 `.SZ` 后缀，老数据不带 → pct_change 全 NaN

### 正确补采步骤
1. 先删不完整的数据：`DELETE FROM stock_prices WHERE date = 'YYYYMMDD'`
2. 插 OHLCV：`pro.daily(trade_date='...')` → 需去掉 `.SZ`/`.SH` 后缀
3. 补 adj_close：`pro.adj_factor(trade_date='...')` → `adj_close = close * adj_factor`
4. 补 total_mv：`pro.daily_basic(trade_date='...')`
5. 股票代码统一格式：**不带后缀**（`300001` 而不是 `300001.SZ`）
6. 日期统一格式：**`YYYYMMDD`** 不带横杠

### 铁律
- ❌ 不用 `pro.daily()` 单独补数据，缺字段
- ✅ 用 `stock_data_collector.py` 的专用接口或完整流程
- ✅ 补数据前先查现有数据的代码和日期格式

