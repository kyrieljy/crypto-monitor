# 架构说明

## 总览

项目是一个本地运行的全栈监控系统：

- 前端负责交易终端式看板、后台配置、二级巨鲸详情页。
- 后端负责配置存储、行情/新闻/策略 worker、通知发送、SSE 实时推送、静态前端托管。
- SQLite 保存业务配置、事件、布局、健康状态和敏感字段密文。

```text
React/Vite UI
  -> REST API / SSE
FastAPI
  -> Store / SQLite
  -> MarketDataRouter -> Binance Futures / OKX
  -> TechnicalStrategyRunner -> KDJ / MA / BOLL
  -> NewsRunner -> Trump RSS / Truthbrush / White House
  -> Translator -> OpenAI-compatible LLM
  -> NotificationWorker -> Feishu / Telegram
  -> Whale API adapter (待实现)
```

## 技术栈

前端：

- React 18
- TypeScript 5
- Vite 6
- React Query
- `react-grid-layout`
- `lightweight-charts`
- `lucide-react`

后端：

- Python
- FastAPI
- Uvicorn
- SQLite
- Pydantic v2
- `requests`
- `python-dotenv`
- `cryptography`
- 后台 `asyncio` worker

## 目录结构

```text
market-monitor-dashboard/
  backend/
    app/
      main.py                 FastAPI 路由、worker 启停、静态前端托管
      api/schemas.py          API 入参/出参模型
      core/
        database.py           SQLite schema、默认数据、迁移
        security.py           管理员 token、敏感字段加密/脱敏
        settings.py           启动级配置
        time.py               时间工具
        text.py               新闻去重文本工具
      services/
        store.py              数据访问层
        market_data.py        行情数据源路由和灾备
        indicators.py         KDJ/MA/BOLL 指标计算
        strategies.py         技术策略轮询
        news.py               新闻源抓取/解析/分类
        news_runner.py        新闻轮询
        translator.py         大模型翻译
        notifiers.py          飞书/Telegram 发送
        notification_worker.py 告警/新闻推送 worker 和机器人模板
        events.py             SSE 事件总线
      tests/
  frontend/
    src/
      App.tsx                 主界面、后台、巨鲸详情页主要逻辑
      styles.css              全局样式和响应式布局
      components/
        CoinChart.tsx         K 线图组件
        Panel.tsx             面板容器
        Switch.tsx            独立开关按钮
      lib/
        api.ts                API client
        format.ts             展示格式化工具
      types/api.ts            前端 API 类型
  data/
    app.db                    SQLite 数据库
  logs/
  README.md
```

## 后端启动与生命周期

`backend/app/main.py` 在模块加载时创建：

- `Database`
- `Store`
- `EventBus`
- `DataSourceRouter`
- `NotificationService`
- `Translator`

FastAPI `startup` 时如果 `RUN_WORKERS` 开启，会启动：

- `TechnicalStrategyRunner`
- `NewsRunner`
- `NotificationWorker`

`shutdown` 时取消 worker 并关闭数据库。

## 数据库表

核心表：

- `symbols`：关注币种。
- `strategy_configs`：KDJ、MA、BOLL、新闻、翻译、巨鲸配置。
- `notifier_targets`：Webhook/Telegram 机器人目标，敏感字段加密。
- `strategy_notifier_bindings`：策略到机器人绑定。
- `dashboard_modules`：看板模块开关、标题、模块级配置。
- `dashboard_layouts`：看板布局和主题。
- `alert_events`：技术策略告警事件。
- `news_events`：新闻/社媒事件，含翻译、去重、通知状态。
- `source_health`：数据源健康状态和最后错误。
- `app_state`：迁移版本、翻译/巨鲸 API key 等状态。
- `whale_targets`：关注的巨鲸/聪明钱地址或对象。
- `whale_events`：巨鲸动作事件骨架表。

## REST API

认证：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/auth/login` | 管理员登录，返回 Bearer token |

配置：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/settings/symbols` | 获取关注币种 |
| `PUT` | `/api/settings/symbols` | 保存关注币种，需登录 |
| `GET` | `/api/strategies/{strategy_id}` | 获取策略配置 |
| `PUT` | `/api/strategies/{strategy_id}` | 保存策略配置，需登录 |
| `GET` | `/api/notifiers` | 获取机器人目标，敏感字段脱敏 |
| `PUT` | `/api/notifiers` | 保存机器人目标，需登录 |
| `POST` | `/api/notifiers/{notifier_id}/test` | 测试机器人，需登录 |
| `GET` | `/api/dashboard/modules` | 获取模块配置 |
| `PUT` | `/api/dashboard/modules` | 保存模块显示配置，需登录 |
| `GET` | `/api/dashboard/layout` | 获取布局 |
| `PUT` | `/api/dashboard/layout` | 保存布局，需登录 |

事件和看板：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/snapshot` | 首页一次性快照 |
| `GET` | `/api/events/alerts` | 技术告警列表 |
| `GET` | `/api/events/news` | 新闻/社媒列表 |
| `POST` | `/api/events/news/translate` | 一键翻译指定新闻，需登录 |
| `GET` | `/api/health/sources` | 数据源健康 |
| `GET` | `/api/market/klines/{symbol}/{interval}` | K 线数据 |
| `GET` | `/api/stream` | SSE 推送新事件和心跳 |

巨鲸：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/whales` | 关注地址列表 |
| `GET` | `/api/whales/{target_id}` | 地址二级详情页数据 |

## 策略架构

KDJ：

- 策略 ID：`kdj`
- 保留 J 上穿/下穿 K。
- 默认参数：`period=26`、`k_smoothing=20`、`d_smoothing=9`。
- 支持多周期。
- 默认按闭合 K 线告警，可配置实时 K 线。

MA：

- 策略 ID：`ma`
- 保留快慢均线穿越。
- 默认 `MA25/MA99`。
- 支持多周期提醒配置。
- 支持实时 K 线开关。

BOLL：

- 策略 ID：`boll`
- 默认 `period=20`、`stddev=2`。
- 检查收盘价上穿上轨或下穿下轨。
- 去重维度：币种 + 周期 + 方向 + K 线时间。
- 支持实时 K 线开关。

## 周期语义

周期有两个不同含义，不能混淆：

- 首页展示周期：前台选择当前看哪个周期的数据和提醒。
- 后台机器人提醒周期：决定哪些周期会推送到机器人。

用户要求前台看板“所有周期都监控，用户选择看”；后台只控制机器人提醒哪些周期。

## 行情数据源

行情通过 `DataSourceRouter` 获取。

当前看板支持主源/灾备概念：

- 币安 Futures 优先。
- OKX 可作为备用。
- 当灾备生效时，前台显示当前生效数据源。

数据源健康写入 `source_health`，前端显示中文解释。

## 新闻架构

Trump 社媒：

- 策略 ID：`trump_social`
- 默认 RSS 优先。
- Truthbrush 可选，默认不建议同时打开以避免重复。
- 分类字段用于机器人推送和看板展示。

White House：

- 策略 ID：`whitehouse`
- 使用 White House Gallery。
- 后台支持 include/exclude keywords。

去重：

- `news_events` 对同源 `event_id` 和 `url` 做唯一约束。
- `content_hash` 和文本相似规则用于跨源合并。

翻译：

- 策略 ID：`translation`
- OpenAI-compatible API。
- 当前模型名应使用 `deepseek-v4-flash`。
- 翻译只处理新闻/社媒，不是通知机器人。

## 通知架构

通知 flow：

```text
alert_events/news_events
  -> NotificationWorker 读取未发送事件
  -> 根据 strategy_notifier_bindings 找 notifier
  -> notification_worker.py 生成机器人专用模板
  -> notifiers.py 发送飞书/Telegram
  -> 写回 notification_sent / attempts / last_notification_error
```

机器人推送模板与前台看板文案分离：

- 前台保持短格式，便于每个币卡片显示。
- 机器人使用详细模板，含标的、周期、信号、指标值、K 线时间、数据源、提醒时间。

## 前端架构

主入口是 `frontend/src/App.tsx`，目前集中承载：

- 应用壳和侧边栏。
- 看板首页。
- 后台登录和管理页。
- 行情与策略监控。
- 新闻模块。
- 巨鲸列表和二级详情页。
- 弹框提示。

重要组件：

- `CoinChart.tsx`：K 线图，基于 `lightweight-charts`。
- `Switch.tsx`：独立开关按钮，避免点击整行误触发。
- `Panel.tsx`：统一面板容器。

响应式和动画主要在 `frontend/src/styles.css`。

## 巨鲸/聪明钱计划架构

当前只完成骨架：

- `whale_targets` 保存关注对象。
- `whale_events` 保存动作事件。
- `/api/whales` 和 `/api/whales/{id}` 返回列表和详情。
- 前端有首页模块和二级详情页。

下一步推荐增加：

- `WhaleProvider` adapter interface。
- provider 配置：`provider`、`base_url`、`api_key`、`poll_seconds`。
- 后台 CRUD：关注地址、标签、启停、通知绑定。
- Worker：按 provider 拉取地址动作、持仓、委托、成交。
- 入库：标准化为 address action、position snapshot、holding snapshot。
- 通知：大额动作、开仓/平仓、加仓/减仓、清仓等模板。

在真实 API 文档确认前，不要伪造真实监控能力。
