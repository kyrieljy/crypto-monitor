# TODO

更新日期：2026-07-15

## P0：下一步优先处理

### 1. 生产环境同步验证

- 在服务器 `/root/crypto-monitor` 拉取最新 `main`。
- 构建前端并重启 systemd。
- 验证生产环境：
  - ETH 大图滚轮缩放和鼠标拖拽。
  - 刷新 K 线不黑屏。
  - 浏览器强刷后不会拿旧资源。
  - Trump 转发卡片描述能显示中文翻译。
  - 巨鲸成交机器人模板不包含地址、链接、大额标记。

### 2. 本地 localhost 黑屏排查

如果本地 `localhost:8800` 仍黑屏：

- 打开浏览器控制台看 JS 报错。
- 确认后端返回的是最新 `frontend/dist/index.html`。
- 确认没有旧 service/cache。
- 查看是否触发 ErrorBoundary。
- 不要恢复 `CoinChart` 中的 `container.innerHTML = ""`。

### 3. 社媒转发翻译补齐

当前新入库转发卡片已经支持 `metadata.card.translated_description`。

待处理：

- 给历史已入库但未翻译 card 的记录增加批量补齐入口，或在用户点“翻译”时补 metadata。
- 对纯图片/视频转发，继续只提示类型和跳转，不强行摘要。
- 转发卡片正文如果是英文，前台应优先显示中文翻译。

### 4. 巨鲸页面继续完善

- 最近成交支持分页或“加载更多”，不要一次性渲染过多。
- 资金流水和历史订单增加筛选。
- 最近动态区区分：
  - 成交
  - 开仓/平仓
  - 仓位变化
  - 强平风险
  - 资金费
  - 出入金/转账
- 巨鲸首页卡片补标签、资产概览、最近动作摘要。

## P1：短期增强

### EVM 大额转账监控

需求：突然买入/转入 100 ETH、链上大额转账等。

待研究：

- Alchemy Free 是否足够：
  - 地址交易历史
  - token transfer
  - webhook 或轮询
  - 免费额度
- Etherscan V2 是否满足补充浏览器链接和交易记录。
- 是否需要按链配置：Ethereum、Base、Arbitrum、Optimism、BSC 等。

第一版建议：

- 只做关注地址的大额转账。
- 不做全链 mempool 扫描。
- 不做智能合约语义复杂归因。
- 事件进入 `whale_events`，复用巨鲸机器人通知。

### DeBank 接入

- DeBank OpenAPI 需要 AccessKey。
- 未配置 AccessKey 时，现货/DeFi tab 显示“未配置数据源”，不要报错。
- 后台巨鲸策略应保留 DeBank AccessKey 配置。
- 接入后展示：
  - 总资产
  - 各链资产
  - 协议仓位
  - token 列表

### 巨鲸数据清理和容量观察

当前清理范围包含 `whale_events`，默认 90 天。

还需要观察：

- `whale_snapshots` 是否会无限增长；当前设计应保留最新快照或覆盖快照。
- 如果后续引入成交明细独立表，需要加入清理策略。
- 2000 条最近成交是快照展示上限，不等于数据库永久保留上限。

### 用户隔离

已确认原则：

- 主题、布局拖拽、周期选择、图表指标模式属于终端用户个人偏好。
- 翻译结果、后台策略、关注对象属于全局共享。

待补齐：

- 检查所有 UI 偏好是否仍写到后端 `dashboard_layouts`。
- 需要的话迁移到 `localStorage`。
- 多终端同时访问时，A 的主题切换不应影响 B。

## P2：中期规划

### 聪明钱标签和实体归因

低成本版：

- 手动添加地址。
- 本地候选库。
- 来源链接可选。

付费增强：

- Nansen Smart Money / Smart HL Perps Trader。
- Arkham entity-first 地址情报。
- DeBank 标签补充。

注意：聪明钱“是谁”不是链上原生事实，属于标签和归因，不能伪造。

### Nginx 和 TLS

当前直接访问 `http://ip:8800`。

后续如果需要 HTTPS：

- 配域名。
- Nginx 反向代理到 `127.0.0.1:8800`。
- Certbot 或云服务证书。
- systemd 仍只管理后端应用。

### 前端拆分

`App.tsx` 已经很大，后续应拆：

- `DashboardPage`
- `AdminPage`
- `MarketStrategyPanel`
- `NewsPanel`
- `WhalePanel`
- `WhaleDetailPage`
- `NotifierEditor`
- `StrategyEditor`

拆分时不要顺手重构业务逻辑，先按边界迁移。

### 监控和运维

- 增加健康检查端点或页面。
- 增加日志轮转说明。
- 增加 systemd 环境文件示例。
- 增加部署脚本，但不要默认执行 destructive 操作。
- 增加数据库大小和清理结果展示。

## 已完成清单

- FastAPI + SQLite 后端。
- React + Vite 前端。
- 系统部署到 `/root/crypto-monitor` 并由 systemd 托管。
- Binance/OKX 行情源和灾备提示。
- KDJ/MA/BOLL 策略。
- BOLL 策略和机器人模板。
- KDJ/MA/BOLL/BOLL中轨-MA 技术指标推送矩阵，可精确选择每个币种的七档提醒周期。
- BOLL 中轨上穿/下穿 MA 策略、独立机器人绑定和前台 BOLL 区域展示。
- 机器人通知绑定和敏感字段加密。
- 新闻社媒抓取、翻译、去重。
- Trump 图片/视频/转发帖详情页补全。
- 前台直接显示社媒图片小预览。
- 转发卡片跳 Truth detail。
- 翻译错误 token 前端过滤。
- 清理策略和 `CleanupWorker`。
- 巨鲸 Hyperliquid provider。
- 巨鲸成交级监控。
- 巨鲸机器人推送。
- 巨鲸关注对象管理迁移到巨鲸模块。
- ETH 大图布局。
- 指标线切换、提示、默认 MA。
- K 线拖拽、滚轮缩放和刷新黑屏修复。

## 明确不做

- 不做跟单交易。
- 不做交易签名。
- 不接私钥。
- 不做钱包授权。
- 不在未确认时触发真实 webhook。
- 不恢复 GNews。
- 不恢复 1 分钟高低差告警。
- 不恢复 1 分钟成交量告警。
- 不把 KDJ/MA/BOLL 做回独立模块。
- 不在清理前做本地数据库备份。
