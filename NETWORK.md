# 服务器公网 IP 与访问说明

## 公网 IP 清单

| 服务器 | 公网 IP | 地域 | 系统 | 用途 |
|--------|---------|------|------|------|
| 首尔 | **43.155.197.236** | 韩国首尔 | Ubuntu 24.04 | Shadowsocks 代理 + DuckDB 数据备份 |
| 小五 | **111.229.134.97** | 中国上海 | Ubuntu 24.04 | OpenClaw Agent + 策略回测 + 巡检 |
| Mac | 192.168.3.47 (内网) | 上海家中 | macOS | L1 实时行情 + Redis + DuckDB 主库 |

## SSH 访问

```bash
# 首尔（通过代理）
ssh -i ~/.ssh/tencent_cloud ubuntu@43.155.197.236

# 小五（上海直连）
ssh -i ~/.ssh/tencent_cloud ubuntu@111.229.134.97

# Mac（仅内网）
ssh jin@192.168.3.47
```

## 服务端口

| 服务 | 位置 | 端口 | 说明 |
|------|------|------|------|
| Shadowsocks | 首尔 | 8388 | 代理端口，chacha20-ietf-poly1305 |
| Redis L1 | Mac 内网 | 6379 | 实时行情数据 |
| OpenClaw | 小五 | 18789 | 仅本地 loopback |
| OpenClaw | Mac | 18789 | 仅本地 loopback |
| Docker Redis | Mac 内网 | 6379 | go-redis-1 容器 |

## 网络拓扑

```
晓东 (手机/电脑)
    │
    ├──→ 首尔 43.155.197.236 (SS/Clash 代理)
    │       └── DuckDB: snapshots + stock_data 备份
    │
    ├──→ 小五 111.229.134.97 (策略/巡检)
    │       └── DuckDB: stock_data 镜像
    │
    └──→ Mac 192.168.3.47 (内网)
            ├── Redis: L1 实时 6580 只股票
            ├── DuckDB: 主库 711 万行
            └── Docker: receiver + writer + redis
```

## 连接规则

- 首尔 ↔ 小五：SSH 互信（密钥认证）
- Mac → 首尔：SSH 密钥认证（走 Clash 代理）
- Mac → 小五：SSH 密钥认证（国内直连）
- 小五 → Mac：**不可达**（内网，需 Mac 主动连接）

## 防火墙（腾讯云安全组）

| 端口 | 来源 | 用途 |
|------|------|------|
| 22 | 0.0.0.0/0 | SSH |
| 8388 | 0.0.0.0/0 | Shadowsocks (首尔) |
