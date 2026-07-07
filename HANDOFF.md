# Handoff

更新日期：2026-07-06

这份文档用于新开 Codex 对话时快速接上当前项目状态。新对话建议先读：

1. `HANDOFF.md`
2. `PROJECT_CONTEXT.md`
3. `ARCHITECTURE.md`
4. `IBIT_FREE_MONITOR.md`
5. `TODO.md`

## 项目位置

- 本地项目：`D:\market-monitor-dashboard`
- GitHub：`https://github.com/kyrieljy/crypto-monitor.git`
- 线上目录：`/root/crypto-monitor`
- 线上访问：`http://167.179.69.248:8800/`
- systemd 服务：`crypto-monitor.service`

## 本地启动

后端：

```powershell
cd D:\market-monitor-dashboard
$env:RUN_WORKERS="true"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8800
```

前端开发服务：

```powershell
cd D:\market-monitor-dashboard\frontend
npm.cmd run dev
```

打开：

```text
http://127.0.0.1:5173/
```

前端 Vite 会把 `/api` 代理到 `http://127.0.0.1:8800`。

## 验证命令

后端完整测试：

```powershell
cd D:\market-monitor-dashboard
.\.venv\Scripts\python.exe -m pytest backend\app\tests
```

前端构建：

```powershell
cd D:\market-monitor-dashboard\frontend
npm.cmd run build
```

最近一次验证结果：

- 后端：`61 passed`
- 前端：`npm run build` 通过

## 当前重点

当前重点是巨鲸/聪明钱模块，尤其是：

- Hyperliquid 常规 0x 巨鲸监控。
- IBIT 免费监控。
- BTC 大额底表。
- 新闻线索和 BTC 大额交易的疑似地址匹配。
- 机器人提醒字段和前台展示体验。

## 地址体系边界

常规巨鲸监控用的是 EVM/Hyperliquid 地址，格式是 `0x...`。

IBIT 免费监控用的是 BTC 链地址，格式可能是：

- `1...`
- `3...`
- `bc1...`

BTC 地址不能直接换算成 0x 地址。只有出现公开归因、桥接记录、交易所出入金证据或同私钥复用证据时，才可能做“疑似关联”。不要把 BTC 地址粘到常规 0x 巨鲸对象里。

正确做法：

- BTC 地址加入 `IBIT 免费监控 -> BTC地址簇`。
- 0x 地址才加入常规巨鲸关注对象。

## IBIT 免费监控现状

目标：不用 Arkham API/信用卡，低成本监控 IBIT。

数据层：

1. iShares 官方 IBIT 页面：净资产、BTC 基准价、估算 BTC 持仓。
2. Farside BTC ETF Flow：日频资金流。
3. BTC 地址簇：人工确认后，用 Blockstream/Mempool 监控逐笔 BTC 链上操作。
4. 新闻线索：RSS/新闻源提取 IBIT、贝莱德、Coinbase、txid、BTC 地址和金额。
5. BTC 大额底表：全网已确认 BTC 大额交易，和新闻按时间、金额、方向做相似匹配。

重要边界：

- 这不是 Arkham 级实体归因。
- 系统不会自动把疑似地址当成已确认地址。
- 疑似地址需要人工判断后点击“加入监控”进入 `btc_addresses`。
- “加入候选”只进入 `suspected_btc_addresses`，不等于正式监控。

## BTC 大额底表

新增能力：

- 后端服务：`backend/app/services/btc_large_transfer.py`
- 测试：`backend/app/tests/test_btc_large_transfer.py`
- API：
  - `GET /api/btc/large-transfers`
  - `GET /api/btc/large-transfers/stats`
  - `GET /api/btc/large-transfers/{txid}`
  - `POST /api/btc/large-transfers/rescan`

配置项在巨鲸策略里：

- `btc_candidate_monitor_enabled`
- `btc_candidate_min_btc`
- `btc_candidate_retention_days`
- `btc_candidate_backfill_blocks`
- `btc_candidate_scan_blocks_per_run`
- `btc_candidate_match_window_hours`
- `btc_candidate_amount_tolerance_pct`
- `btc_candidate_max_matches_per_news`

前端位置：

```text
IBIT 免费监控 -> BTC大额底表
```

页面支持：

- 查看大额交易底表。
- 搜索 txid / BTC 地址。
- 只看新闻命中。
- 管理员补扫最近 3 块。
- 管理员按历史时间段回扫。
- 对输入/输出 BTC 地址点击：
  - `加入监控`：写入 `btc_addresses`。
  - `加入候选`：写入 `suspected_btc_addresses`。

## 已跑通的贝莱德案例

新闻截图：

```text
贝莱德 ETF 地址向 Coinbase 存入 4917 枚 BTC，价值约 3.01 亿美元
时间：07/02 19:22 北京时间
```

链上匹配结果：

- 区块：`956347`
- 时间：`2026-07-02 11:22:04 UTC`
- txid：`a00ec5e8dba31bc49c9b49ee2e551ea63b6736be7948933d128033199b1aa384`
- 输入：`4916.50920762 BTC`
- 新闻金额：`4917 BTC`
- 高置信疑似源地址：`36YZXcTVLPdyapYuqXdJEt46oMVB2NrzVv`

判断含义：

- 这是 BTC 地址，不是 0x 地址。
- 它适合加入 IBIT 的 BTC 地址簇监控。
- 它不能直接放进 Hyperliquid/EVM 常规巨鲸监控。
- 该结论是行为相似度匹配，不是付费实体归因证明。

## 最近 UI 调整

IBIT 页面：

- 顶部 tab 保留：
  - 基本信息
  - ETF资金流
  - BTC地址簇
  - BTC大额底表
  - 疑似地址
  - 新闻线索
  - 最近动态
- 新闻线索卡片已优化：
  - 标题、时间、置信度、金额分层展示。
  - 金额和 txid 信息用可换行标签。
  - 右侧“查看匹配/收起”固定宽度。
- BTC 大额底表详情已优化：
  - 明确提示 BTC 地址不是 0x 地址。
  - 地址旁有“加入监控”和“加入候选”。

后台关注对象：

- `IBIT 免费监控` 行的 BTC 地址输入已移到第二行。
- `已确认 BTC 地址簇` 和 `候选 BTC 地址池` 不再挤压保存/删除按钮。

## 当前未提交变更

当前工作树有较多未提交变更，包含本轮和之前上下文整理的内容。新对话接手前应先运行：

```powershell
cd D:\market-monitor-dashboard
git status --short
```

已知新增文件包括：

- `IBIT_FREE_MONITOR.md`
- `HANDOFF.md`
- `backend/app/services/btc_large_transfer.py`
- `backend/app/tests/test_btc_large_transfer.py`

已知主要修改文件包括：

- `backend/app/api/schemas.py`
- `backend/app/core/database.py`
- `backend/app/main.py`
- `backend/app/services/store.py`
- `backend/app/services/whale.py`
- `backend/app/services/whale_runner.py`
- `frontend/src/App.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/types/api.ts`
- `frontend/src/styles.css`

不要随意 revert 未确认的既有变更。

## 下一步建议

1. 本地再用页面复现贝莱德 4917 BTC 案例：
   - `IBIT 免费监控 -> BTC大额底表`
   - 历史开始：`2026-07-02 19:00`
   - 历史结束：`2026-07-02 20:00`
   - 最多区块：`24`
   - 点击“历史回扫”
   - 搜索 `36YZX` 或 txid。
2. 点击 `36YZXcTVLPdyapYuqXdJEt46oMVB2NrzVv` 的“加入监控”，进入已确认 BTC 地址簇。
3. 观察 `BTC地址簇` tab 是否展示该地址最近链上操作。
4. 确认交互和文案后，再统一提交并推送。

