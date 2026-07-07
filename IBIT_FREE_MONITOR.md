# IBIT 免费监控工作流

## 目标

不用 Arkham API 和信用卡，先实现对 IBIT 的低成本监控。

这个方案不是 Arkham 级完整实体归因。它由三层数据组成：

1. iShares 官方 IBIT 页面：确认 IBIT 日频净资产、基准 BTC 价格，并估算 BTC 持仓。
2. Farside BTC ETF Flow：监控 IBIT 每日资金流入/流出。
3. 已确认 BTC 地址簇：你手工确认地址后，系统用免费 Blockstream Esplora API 监控这些 BTC 地址的大额转出。
4. 新闻线索：从配置的 RSS/新闻源中提取 IBIT、贝莱德、Coinbase、txid、BTC 地址和金额，生成“疑似地址簇线索”。

## 数据源边界

- iShares 和 Farside 是日频数据，适合确认 IBIT 是否发生大额申赎或持仓变化。
- Blockstream 只能监控你已经填入的 BTC 地址，不能自动判断一个新地址是否属于 IBIT 或 BlackRock。
- 新闻线索只用于发现候选 txid / BTC 地址，系统不会自动把疑似地址加入已确认地址簇。
- 系统内显示为“IBIT 免费监控”或“已确认 BTC 地址簇”，不把它标成 Arkham 级 BlackRock 实体归因。
- “BTC 地址簇”展示的是已确认地址的最近逐笔链上操作；“疑似地址”展示的是新闻/txid 反查或你填入的疑似地址池和新闻行为匹配后的候选地址、操作与置信度。
- 免费 Blockstream API 不能按“BlackRock/IBIT 实体”拉取全部地址；必须先有候选地址来源，例如新闻 txid、Arkham 网页人工复制、OnchainLens/Lookonchain 链接，或你手工维护的疑似地址池。

## 本地启动

```powershell
cd D:\market-monitor-dashboard
$env:RUN_WORKERS="true"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8800
```

打开：

```text
http://127.0.0.1:8800
```

## 后台配置

进入后台，打开“巨鲸”策略：

1. 启用“巨鲸”。
2. 启用“IBIT 免费监控”。
3. 保持默认数据源：
   - iShares IBIT 页面：https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf
   - Farside ETF Flow：https://farside.co.uk/btc/
   - BTC Explorer API：https://blockstream.info/api
4. 默认提醒阈值：
   - IBIT 资金流提醒美元：50000000
   - BTC 地址簇转出阈值：1000 BTC
   - BTC 地址簇回看小时：24
5. 如需新闻线索，启用“IBIT 新闻线索”，并配置 RSS 地址和关键词。

新闻 RSS 可以使用你信任的公开源或自建 RSSHub 源。系统会读取 RSS item 的标题、摘要、链接和发布时间。

## 启用默认对象

服务重启后，系统会自动生成一个默认对象：

```text
IBIT 免费监控
```

默认是关闭状态。确认策略配置后，在巨鲸关注对象里启用它并保存。

保存后，后台会立刻同步一次：

- iShares 官方数据
- Farside IBIT Flow
- 你已配置 BTC 地址簇最近 24 小时的大额转出
- 你已配置新闻源中的 IBIT 新闻线索

## 如何添加 BTC 地址簇

地址来源必须是你确认过的公开线索，例如：

- 新闻里给出的 txid。
- Arkham 网页人工看到的 BlackRock/IBIT 钱包地址。
- OnchainLens、Lookonchain、Ai 姨等公开消息里的链上链接。

操作方式：

1. 后台进入“巨鲸”对象。
2. 找到 `IBIT 免费监控`。
3. 在对象配置的 `btc_addresses` 里加入 BTC 地址。
4. 保存对象。

注意：不要把未经确认的交易所热钱包、Coinbase 汇总钱包直接标成 BlackRock 地址。

## 如何添加疑似地址池

疑似地址池用于“先观察、再确认”：

1. 后台进入“巨鲸”对象。
2. 找到 `IBIT 免费监控`。
3. 在对象配置的“疑似 BTC 地址池”中加入候选 BTC 地址。
4. 保存对象。

系统会拉取这些地址最近回看窗口内的逐笔链上操作，并和新闻里的 BTC 数量、发布时间、Coinbase/流入流出等行为做相似度匹配。匹配结果显示在“疑似地址”模块；确认后再把地址移动到 `btc_addresses`。

## 告警含义

### blackrock_etf_flow

Farside 出现新的 IBIT 日资金流，且绝对值超过配置的美元阈值。

### blackrock_official_update

iShares 官方 IBIT 页面日期更新。这个事件默认只进入最近动态，不推机器人。

### blackrock_confirmed_btc_outflow

已确认 BTC 地址簇发生超过阈值的 BTC 净转出。这个只说明“已确认地址簇转出”，不自动断言收款方是 Coinbase。

### ibit_news_address_candidate

新闻中出现 IBIT / 贝莱德 / Coinbase / txid / BTC 地址 / 金额等线索。系统会：

- 提取 txid、BTC 地址、BTC 数量和美元金额。
- 如有 txid，调用 Blockstream 反查输入地址和输出地址。
- 和最新 IBIT 资金流金额做粗略相似度比较。
- 生成置信度和理由。

这个事件只表示“疑似线索”，需要人工打开新闻和链上链接确认后，再把地址加入 `btc_addresses`。

## 推荐日常流程

1. 每天看“最近动态”里的 IBIT Flow 和官方数据是否更新。
2. 看到新闻说 BlackRock / IBIT 有链上动作时，先找 txid 或链上地址；系统里按 IBIT 线索处理。
3. 看“新闻线索”里的候选 txid、疑似地址和置信度理由。
4. 看“疑似地址”模块，把地址、新闻行为、txid、金额和置信度对应起来。
5. 用 Blockstream / mempool / Arkham 网页人工确认地址。
6. 把确认过的 BTC 地址加入 `btc_addresses`。
7. 系统后续自动监控这些地址的大额转出，并在“BTC 地址簇”里展示逐笔操作。

## 后续可升级方向

- 接 mempool.space 作为 Blockstream 的备用 BTC explorer。
- 增加“待确认 txid 线索”录入页，自动解析 txid 相关地址。
- 如果以后有预算，再切回 Arkham API，恢复完整实体归因。
