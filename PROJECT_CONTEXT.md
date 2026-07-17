# Project Context

更新日期：2026-07-17

## 项目目标

`crypto-monitor` 是一个加密市场实时监控看板，目标是把行情策略、新闻社媒、机器人通知、巨鲸/聪明钱监控、后台配置和服务器运维整合成一个可长期运行的系统。

当前核心目标：

- 前台第一屏直接展示可操作的监控体验，不做营销页。
- 后台统一管理币种、策略参数、通知机器人、新闻/翻译、清理策略、巨鲸数据源。
- 服务启动后，只要 `RUN_WORKERS=true`，后台 worker 自动轮询并推送机器人消息，不依赖浏览器打开前端网页。
- 服务器容量有限，所以需要自动清理数据库，不做本地备份。
- 巨鲸/聪明钱第一版只做只读监控，不做跟单、下单、授权钱包或交易 API。

## 当前仓库和部署

- 本地项目：`D:\market-monitor-dashboard`
- GitHub：`https://github.com/kyrieljy/crypto-monitor.git`
- 主分支：`main`
- 服务器项目：`/root/crypto-monitor`
- 服务器访问：`http://167.179.69.248:8800/`
- systemd 服务：`crypto-monitor.service`
- 服务命令：`/root/crypto-monitor/.venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8800`
- 前端构建目录：`frontend/dist`
- 后端入口：`backend.app.main:app`

最近关键提交：

- `13864d6 Add whale monitoring and chart improvements`
- `3a56915 Fix chart refresh interactions and frontend cache`
- `08ee8b6 Fix local chart render crash`
- `ac3acba Translate repost card content`

## 技术栈

后端：

- Python
- FastAPI
- Uvicorn
- SQLite
- Pydantic
- pytest
- 后台 asyncio worker

前端：

- React 18
- TypeScript
- Vite
- TanStack Query
- lightweight-charts
- lucide-react
- react-grid-layout

运行环境：

- 本地 Windows 开发
- Ubuntu 服务器 + systemd
- Node 需要 20+，Node 12 会导致 TypeScript/Vite 构建失败

## 已完成能力

### 行情与策略

- 默认关注：`BTCUSDT`、`ETHUSDT`、`SOLUSDT`、`BNBUSDT`、`ZECUSDT`。
- 行情源支持 Binance Futures 主源和 OKX 灾备。
- 前台最近告警和行情工具条显示来源，例如“币安 Futures 优先，OKX 备用”“主源 币安”“灾备 OKX”。
- KDJ、MA、BOLL 以及 BOLL 中轨/MA 交叉策略都已接入 worker、前台展示、后台配置和机器人模板。
- 技术策略使用 `notify_intervals_by_symbol` 精确控制“指标 × 币种 × 周期”的机器人推送组合；未选组合仍保留前台固定周期告警。
- 后台技术指标推送矩阵支持 KDJ、MA、BOLL、BOLL中轨/MA 四个标签页以及七档周期，停用币种的配置会保留。
- 机器人提醒已移除“K线时间”字段。
- BOLL 机器人提醒顺序为上轨、中轨、下轨。
- 策略卡片点击后弹窗显示明细，包含 KDJ 值、MA 值、BOLL 上中下轨、当前价/收盘价、数据源、提醒时间等。

### K 线图和布局

- 前台标题为 `Crypto Monitor`。
- ETH K 线大图置顶放大，右侧显示 ETH 的 KDJ/MA/BOLL 预警；BOLL 区域同时汇总轨道突破和中轨/MA 交叉。
- 其余四个币种在下方按原样并排展示。
- ETH 大图支持 MA/BOLL/KDJ 指标线切换。
- 默认只显示 MA 组，包含 MA7、MA25、MA99。
- BOLL 和 KDJ 需要用户主动切换，不和 MA 同时显示。
- 指标线有颜色标注和鼠标提示说明；BOLL 颜色已经做区分。
- K 线图支持鼠标拖拽平移和滚轮缩放。
- K 线请求根数已提高到 600，后端限制为 20 到 1000。
- 已修复刷新 K 线后黑屏问题：不要再用 `container.innerHTML = ""` 清空 lightweight-charts 容器。
- 已给 `index.html` 加 no-cache，避免生产环境刷新后拿到过期资源 hash。

### 新闻和特朗普社媒

- Trump RSS 默认优先，Truthbrush 保留为可选，避免双源默认重复。
- RSS 描述不再把 HTML 原样作为摘要推送。
- 对无标题、空正文、纯链接、转发、媒体帖，后端会抓取详情页补全媒体和卡片信息。
- `news_events.metadata_json` 使用稳定字段：
  - `content_kind`
  - `original_url`
  - `original_id`
  - `links`
  - `media`
  - `card`
- 图片直接在前台小尺寸完整显示，使用缩小预览，不截取。
- 视频如果只有缩略图，显示预览图和“视频”标记。
- 转发/链接卡片使用紧凑预览。
- 没有文字的媒体帖显示“图片/视频/转发内容，点击查看原帖”。
- Truth Social 跳转应优先使用 `https://truthsocial.com/@realDonaldTrump/posts/{id}`。
- 前端会过滤翻译失败的提示词/解释 token，不把“请提供英文新闻标题或摘要”等错误文本展示为正文。
- 已补充转发卡片描述翻译：`metadata.card.translated_description`。

### 通知机器人

- 支持飞书和 Telegram 类型。
- 敏感字段加密存储，API 返回 masked 值。
- 通知目标和策略绑定在后台配置。
- 翻译策略、清理策略不绑定机器人。
- 未经用户明确确认，不触发真实 webhook 测试。
- 机器人消息保持文本模式，不直接发送图片二进制。

### 清理策略

- 新增 `CleanupWorker`。
- 只在 `RUN_WORKERS=true` 时运行。
- 默认每天北京时间 `12:30` 检查并清理。
- 通过 `app_state.cleanup_last_run_date` 防止同一天重复执行。
- 默认保留：
  - 告警 30 天
  - 新闻 60 天
  - 巨鲸事件 90 天
- 默认删除未推送成功的过期数据。
- 删除后默认执行 SQLite `VACUUM` 释放磁盘空间。
- 不清理 `strategy_configs`、`notifier_targets`、`dashboard_*`、`app_state`、`source_health`。
- 不做本地数据库备份，容量优先。

### 巨鲸与聪明钱

第一版定位：只读监控，不做跟单。

已完成：

- `whale_targets`：关注对象、地址、标签、来源链接、启用状态。
- `whale_snapshots`：最新快照，保存持仓、资产、协议仓位、当前委托、成交、资金流水、历史订单、费用等。
- `whale_events`：动态事件，支持去重和机器人推送状态。
- `WhaleRunner`：在 `RUN_WORKERS=true` 时轮询关注对象。
- 巨鲸关注对象管理已经从后台策略配置挪到巨鲸模块。
- 标签改为下拉勾选，默认“聪明钱”，不是自由手填。
- 来源链接不是必填，只用于展示和溯源，不影响监控。
- 当前重点地址：麻吉大哥 `0x020ca66c30bec2c4fe3861a94e4db4a498a35872`。

Hyperliquid 已接入：

- `clearinghouseState`
- `allMids`
- `metaAndAssetCtxs`
- `frontendOpenOrders`
- `historicalOrders`
- `userFills`
- `userFillsByTime`
- `userFunding`
- `userNonFundingLedgerUpdates`
- `portfolio`
- `userFees`

巨鲸机器人推送规则：

- 关注地址对象有新成交操作就提醒。
- 后台阈值只用于前台“大额”标记，不再作为推送门槛。
- 首次同步只建立游标和前台展示，不补发历史机器人通知，避免刷屏。

巨鲸机器人模板当前要求：

```text
[Hyperliquid成交提醒]
对象: 麻吉大哥
币种: ETH
仓位动作: 买入开多
数量: 100 ETH
开仓价格: $2,100.00
杠杆: 25x 全仓
成交额: $210,000
手续费: ...
已实现盈亏: ...
时间: 2026-05-26 15:30:00 CST
```

模板禁止包含：

- 地址
- 链接
- 大额标记：是/否

仓位动作中文映射：

- `Open Long` -> 买入开多
- `Close Long` -> 卖出平多
- `Open Short` -> 卖出开空
- `Close Short` -> 买入平空

## 关键解释

### RUN_WORKERS=true 是什么

`RUN_WORKERS=true` 表示 FastAPI 启动时同时启动后台轮询任务：

- 技术策略 worker
- 新闻 worker
- 通知 worker
- 清理 worker
- 巨鲸 worker

生产环境应该默认开启。关闭后前端 API 仍可访问，但不会自动轮询策略、新闻、清理和推送。

### K 线时间和提醒时间为什么会差很多

K 线时间是蜡烛的开盘时间或周期时间，不是发送提醒的时间。提醒时间取决于 worker 什么时候轮询到该根 K 线、数据源是否延迟、是否刚启动补拉、是否走灾备源、以及通知 worker 排队时间。所以有时非常接近，有时会差几十分钟。

### Hyperliquid SSL EOF 是不是限频

`SSLEOFError EOF occurred in violation of protocol` 更像网络/TLS 或对端连接中断，不一定是请求次数限制。但为了安全，已经按保守频率处理：

- 主轮询不低于 300 秒
- 成交轮询不低于 120 秒
- 扩展信息轮询不低于 1800 秒

## 服务器运维速记

常规更新：

```bash
cd /root/crypto-monitor
git pull
. .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend
npm run build
cd ..
sudo systemctl restart crypto-monitor
sudo systemctl status crypto-monitor --no-pager
```

如果前端依赖有变化，再运行：

```bash
cd /root/crypto-monitor/frontend
npm ci --include=dev
npm run build
```

不需要每次都删除 `node_modules`。如果服务器容量紧张，构建后可以删除，但下次构建会重新安装依赖。

查看日志：

```bash
sudo journalctl -u crypto-monitor -f
```

停止服务：

```bash
sudo systemctl stop crypto-monitor
```

开机自启：

```bash
sudo systemctl enable crypto-monitor
```

## 用户关键约束

- 不做跟单按钮。
- 不做交易、签名、钱包授权、私钥管理。
- 不触发真实 webhook，除非用户单独确认。
- 不做本地数据库备份，服务器容量优先。
- 不显示原始 HTML。
- 不把翻译失败提示展示到前台正文。
- 不在机器人提醒里显示 K 线时间。
- 不让主题、拖拽布局、周期选择等用户个人 UI 操作互相影响；只有翻译和后台策略这类全局配置共享。
- KDJ/MA/BOLL 仍属于行情与策略监控内部，不恢复成独立模块。
- 前台设计保持密集、专业、交易终端风格。

## 当前最重要的后续方向

1. 确认生产环境已拉取最新提交并重新构建，尤其是图表拖拽/滚轮、no-cache、社媒转发翻译。
2. 继续完善巨鲸页：成交、资金流水、历史订单、最近动态的筛选和分页。
3. 做 EVM 大额转账监控方案，优先评估 Alchemy Free。
4. 增加社媒转发卡片历史数据的翻译补齐或后台批处理。
5. 后续有预算再接 DeBank/Nansen/Arkham，增强多链资产和聪明钱标签。
