# Architecture

更新日期：2026-05-26

## 总览

系统是一个单进程 FastAPI 应用，负责：

- 提供 REST API 和 SSE。
- 托管 Vite 构建后的静态前端。
- 启动后台 worker 轮询行情、策略、新闻、通知、清理和巨鲸数据。
- 使用 SQLite 保存配置、事件、快照、状态和布局。

```text
Browser
  -> React / Vite static assets
  -> REST API /api/*
  -> SSE /api/stream

FastAPI backend
  -> Store
  -> SQLite
  -> Market providers: Binance Futures / OKX
  -> News providers: Trump RSS / Truth detail / White House
  -> Whale providers: Hyperliquid / DeBank optional
  -> Notification providers: Feishu / Telegram
```

## 目录结构

```text
market-monitor-dashboard/
  backend/
    app/
      main.py
      api/
        schemas.py
      core/
        database.py
        security.py
        settings.py
        text.py
        time.py
      services/
        cleanup_worker.py
        events.py
        indicators.py
        market_data.py
        news.py
        news_runner.py
        notification_worker.py
        notifiers.py
        strategies.py
        store.py
        translator.py
        whale.py
        whale_runner.py
      tests/
  frontend/
    src/
      App.tsx
      main.tsx
      styles.css
      components/
        CoinChart.tsx
        Panel.tsx
        Switch.tsx
      lib/
        api.ts
        format.ts
      types/
        api.ts
    dist/
  data/
    app.db
```

## 后端生命周期

`backend/app/main.py` 在应用启动时创建核心对象：

- `Database`
- `Store`
- `EventBus`
- `DataSourceRouter`
- `NotificationService`
- `Translator`

当 `RUN_WORKERS=true` 时，FastAPI startup 会启动：

- `TechnicalStrategyRunner`
- `NewsRunner`
- `NotificationWorker`
- `CleanupWorker`
- `WhaleRunner`

shutdown 时会取消这些任务并关闭数据库连接。

## 配置边界

`.env` 只放启动级配置：

- `DATABASE_PATH`
- `HOST`
- `PORT`
- `APP_SECRET_KEY`
- `ADMIN_PASSWORD`
- `RUN_WORKERS`
- `REQUEST_TIMEOUT_SECONDS`

业务配置都存 SQLite 并由后台页面管理：

- 币种
- KDJ/MA/BOLL 参数
- 新闻源
- 翻译配置
- 清理策略
- 巨鲸 provider 配置
- 机器人目标和绑定
- 看板模块和布局

敏感字段用 `APP_SECRET_KEY` 加密保存，API 返回 masked 值。

## SQLite 数据模型

核心表：

- `symbols`：关注币种。
- `strategy_configs`：策略配置，包括 `kdj`、`ma`、`boll`、`trump_social`、`whitehouse`、`translation`、`cleanup`、`whale`。
- `notifier_targets`：机器人目标，含飞书/Telegram 敏感字段密文。
- `strategy_notifier_bindings`：策略和机器人绑定。
- `dashboard_modules`：模块开关、标题、模块级配置。
- `dashboard_layouts`：布局和主题。
- `alert_events`：技术策略告警。
- `news_events`：新闻/社媒事件，含翻译、去重、通知状态、`metadata_json`。
- `source_health`：行情/新闻/巨鲸 provider 健康状态。
- `app_state`：全局状态，例如迁移版本、清理日期、Hyperliquid 游标。
- `whale_targets`：关注对象和地址。
- `whale_snapshots`：关注对象最新快照。
- `whale_events`：巨鲸动态事件，含去重 key 和通知状态。

`whale_events` 已增加：

- `event_key`
- `notification_required`
- `notification_sent`
- `notification_attempts`
- `last_notification_error`

## REST API

认证：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/auth/login` | 管理员登录，返回 bearer token |

看板：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/snapshot` | 首页完整快照 |
| `GET` | `/api/stream` | SSE 事件流 |
| `GET` | `/api/health/sources` | 数据源健康 |
| `GET` | `/api/market/klines/{symbol}/{interval}?limit=600` | K 线数据 |

事件：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/events/alerts` | 技术告警列表 |
| `GET` | `/api/events/news` | 新闻/社媒列表 |
| `POST` | `/api/events/news/translate` | 手动翻译新闻，需登录 |

后台配置：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/settings/symbols` | 获取币种 |
| `PUT` | `/api/settings/symbols` | 保存币种，需登录 |
| `GET` | `/api/strategies/{strategy_id}` | 获取策略 |
| `PUT` | `/api/strategies/{strategy_id}` | 保存策略，需登录 |
| `GET` | `/api/notifiers` | 获取机器人目标 |
| `PUT` | `/api/notifiers` | 保存机器人目标，需登录 |
| `POST` | `/api/notifiers/{id}/test` | 测试机器人，需登录，不能未经确认调用真实 webhook |
| `GET` | `/api/dashboard/modules` | 获取模块配置 |
| `PUT` | `/api/dashboard/modules` | 保存模块配置，需登录 |
| `GET` | `/api/dashboard/layout` | 获取布局 |
| `PUT` | `/api/dashboard/layout` | 保存布局，需登录 |

巨鲸：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/whales` | 关注对象列表 |
| `POST` | `/api/whales/resolve` | 从地址/链接/昵称解析候选 |
| `PUT` | `/api/whales` | 保存关注对象 |
| `DELETE` | `/api/whales/{target_id}` | 删除关注对象 |
| `GET` | `/api/whales/{target_id}` | 巨鲸详情 |

## 行情和策略架构

`DataSourceRouter` 负责行情源路由：

- Binance Futures 优先。
- OKX 作为备份。
- 失败时写入 `source_health`。
- 前端显示当前生效源。

策略：

- KDJ：J 上穿/下穿 K。
- MA：快慢均线穿越，当前主要是 MA25/MA99，图表也显示 MA7。
- BOLL：收盘价上穿上轨或下穿下轨。

策略事件进入 `alert_events`。通知 worker 根据绑定关系发送到机器人。

前台展示周期和后台推送周期是两个概念：

- 前台展示周期：用户当前看哪个周期。
- 后台推送周期：哪些周期会触发机器人提醒。

## 新闻和社媒架构

新闻 worker 拉取：

- Trump Truth RSS
- Truth detail page enrich
- White House source

解析流程：

```text
RSS/source item
  -> HTML text extraction
  -> suspicious media/repost/link detection
  -> detail page enrich
  -> metadata_json media/card/original_url/original_id
  -> de-duplicate
  -> optional translation
  -> news_events
  -> NotificationWorker
```

`metadata_json` 约定：

```json
{
  "content_kind": "text|image|video|repost|link|media",
  "original_url": "https://truthsocial.com/@realDonaldTrump/posts/...",
  "original_id": "...",
  "links": [],
  "media": [],
  "card": {
    "title": "",
    "description": "",
    "translated_description": "",
    "url": "",
    "image": ""
  }
}
```

前端优先展示：

1. 翻译正文。
2. 可用中文摘要。
3. 卡片翻译描述。
4. 媒体/转发提示。

前端必须过滤翻译失败提示词，不显示模型错误解释。

## 巨鲸架构

第一版 provider：

- Hyperliquid：公开只读 Info endpoint，合约主源。
- DeBank：多链资产/DeFi，可选，需要 AccessKey。
- Etherscan/Alchemy：后续用于 EVM 大额转账。
- Nansen/Arkham：后续用于聪明钱标签和实体归因。

数据流：

```text
whale_targets
  -> WhaleRunner
  -> HyperliquidProvider
  -> whale_snapshots latest state
  -> whale_events normalized events
  -> NotificationWorker
```

Hyperliquid provider 当前能力：

- 持仓：`clearinghouseState`
- 价格补全：`allMids`
- 标记价格/资金费/OI：`metaAndAssetCtxs`
- 当前委托：`frontendOpenOrders`
- 历史订单：`historicalOrders`
- 最近成交：`userFills`
- 增量成交：`userFillsByTime`
- 资金费：`userFunding`
- 出入金/转账流水：`userNonFundingLedgerUpdates`
- 账户组合：`portfolio`
- 手续费：`userFees`

去重：

- 成交事件用 `target_id + address + hash/tid/oid/time` 生成稳定 `event_key`。
- 首次同步只建立游标，不补发历史通知。
- 后续增量事件进入 `whale_events`。

轮询频率保守值：

- 主轮询最小 300 秒。
- 成交轮询最小 120 秒。
- 扩展信息轮询最小 1800 秒。

## 清理架构

`CleanupWorker` 每分钟检查一次本地时间是否达到配置时间。

默认配置：

```json
{
  "enabled": true,
  "schedule_time": "12:30",
  "timezone": "Asia/Shanghai",
  "alert_retention_days": 30,
  "news_retention_days": 60,
  "whale_retention_days": 90,
  "delete_pending_notifications": true,
  "vacuum_after_cleanup": true
}
```

清理范围：

- `alert_events.created_at`
- `news_events.published_at_utc`
- `whale_events.occurred_at_utc`

不清理：

- 策略配置
- 机器人目标
- 看板配置
- app state
- 数据源健康
- 最新巨鲸快照

## 前端架构

主文件仍集中在 `frontend/src/App.tsx`，包含：

- App shell
- Dashboard
- Admin
- Market strategy panel
- News panels
- Whale list/detail
- Dialogs

重要组件：

- `CoinChart.tsx`：K 线图和指标线，基于 lightweight-charts。
- `Panel.tsx`：通用面板。
- `Switch.tsx`：独立开关，避免整行误触。

用户个人 UI 状态应保存在本地浏览器：

- 主题
- 拖拽布局
- 当前周期
- 图表指标模式

全局状态才保存到后端：

- 策略配置
- 机器人配置
- 翻译结果
- 新闻元数据
- 关注对象

## 部署架构

当前没有 Nginx/TLS，直接由 Uvicorn 对外暴露 8800。

因此：

- 正确访问：`http://167.179.69.248:8800/`
- `https://167.179.69.248:8800/` 不可用，除非后续配置 TLS 或反向代理。

systemd 负责后台常驻、开机自启、崩溃重启。

## 测试入口

后端：

```powershell
D:\market-monitor-dashboard\.venv\Scripts\python.exe -m pytest D:\market-monitor-dashboard\backend\app\tests -q
```

前端：

```powershell
cd D:\market-monitor-dashboard\frontend
npm.cmd run build
```

服务器：

```bash
cd /root/crypto-monitor/frontend
npm run build
sudo systemctl restart crypto-monitor
sudo systemctl status crypto-monitor --no-pager
```
