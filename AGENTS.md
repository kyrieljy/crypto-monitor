# 给下一个 Codex 窗口的说明

## 当前要接的任务

用户准备重新开一个窗口继续做“巨鲸和聪明钱”功能。

请先读：

1. `PROJECT_CONTEXT.md`
2. `ARCHITECTURE.md`
3. `TODO.md`
4. `DECISIONS.md`
5. 本文件

当前巨鲸/聪明钱只是 UI 壳。不要把它当成真实监控已经完成。

## 项目位置

- 开发工作区：`C:\Users\54901\Documents\Playground\market-monitor-dashboard`
- 运行项目：`D:\market-monitor-dashboard`
- 本地页面：`http://127.0.0.1:8800/`

通常先改 Playground，再同步到 D 盘运行项目。

D 盘不在默认可写根目录，写入或同步需要提权。

## 常用命令

前端构建：

```powershell
cd C:\Users\54901\Documents\Playground\market-monitor-dashboard\frontend
npm.cmd run build
```

后端测试：

```powershell
C:\Users\54901\Documents\Playground\market-monitor-dashboard\.venv\Scripts\python.exe -m pytest C:\Users\54901\Documents\Playground\market-monitor-dashboard\backend\app\tests -q
```

D 盘运行项目测试：

```powershell
D:\market-monitor-dashboard\.venv\Scripts\python.exe -m pytest D:\market-monitor-dashboard\backend\app\tests -q
```

启动后端：

```powershell
cd D:\market-monitor-dashboard
.\.venv\Scripts\python.exe -m backend.app
```

如果需要重启 8800 端口的后端，先查进程，再停止对应 Python 进程，不要误杀无关进程。

## 工作规则

- 用中文回复用户。
- 用户通常希望直接实现，不只是给方案。
- 手动编辑文件用 `apply_patch`。
- 搜索文件优先用 `rg` 或 `rg --files`。
- 不要回滚用户未要求回滚的改动。
- 不要使用破坏性命令，例如 `git reset --hard`。
- 不要在没确认时发送真实 webhook 测试。
- 前端改动后要构建并尽量用浏览器验证。
- 后端改动后要跑相关测试，并同步/重启 D 盘服务。
- 如果只是文档改动，不需要重启服务。

## 浏览器验证注意事项

Codex in-app browser 可以用于 `http://127.0.0.1:8800/` 验证。

已知限制：

- Browser evaluate 环境不能直接访问 `localStorage`/`sessionStorage`。
- 自动输入登录时曾因 virtual clipboard 缺失失败。
- 如果需要后台验证，优先让用户保持已登录状态或用页面现状验证。

## 后台登录

- 默认管理员密码是 `change-me-admin`，除非环境变量覆盖。
- 当前逻辑：点击后台先进入登录页，登录后才能看到内容。
- 登出功能已存在。

## 重要 UI 约束

- 品牌显示：`Crypto Monitor`。
- 首页顶部标题：`实时监控看板`。
- 不显示顶部说明小字。
- 行情模块标题：`行情与策略监控`。
- 社媒模块标题：`特朗普社媒监控`。
- 第一行行情与策略监控固定展示，不拖拽，不出现内部滚动条。
- 侧边栏收起后主内容保持原宽度居中，不要扩成满屏。
- 默认布局：
  - 第一行：行情与策略监控
  - 第二行：巨鲸与聪明钱动态
  - 第三行：特朗普社媒监控 + 白宫发言新闻
  - 第四行：最近告警 + 数据源健康
- KDJ/MA/BOLL 不要作为独立模块出现在模块显示列表里。
- KDJ/MA/BOLL 是每个币下面的分类卡。
- 每个策略卡默认 3 条，超过 3 条才允许“更多”展开到 10 条。
- 展开只影响当前卡片高度，不影响 K 线图，也不影响其他卡片。
- 策略卡文案不要换成机器人长模板。

## 策略周期约束

这是用户反复强调过的点：

- 前台看板周期：用户选择当前看什么周期。
- 后台机器人提醒周期：决定哪些周期推送机器人。

不要把两者混在一起。

## 机器人通知约束

前台短文案和机器人长模板分离。

机器人 MA 模板形态：

```text
[MA预警]
标的: SOLUSDT
周期: 1h
信号: MA25下穿MA99
收盘价: 84.9400
快线MA: 85.7076
慢线MA: 85.7248
K线时间: 2026-05-25 07:00:00 CST
数据源: 主源 (binance_futures)
提醒时间: 2026-05-25 07:00:04 CST
```

机器人 KDJ 模板形态：

```text
[KDJ预警]
标的: ETHUSDT
周期: 1h
信号: J上穿K
收盘价: 2110.5900
K: 53.8154
D: 53.8132
J: 53.8198
K线时间: 2026-05-25 13:00:00 CST
数据源: 主源 (binance_futures)
提醒时间: 2026-05-25 13:18:33 CST
```

BOLL 和白宫新闻已按同样思路实现。修改时先看 `backend/app/services/notification_worker.py` 和 `backend/app/tests/test_notification_templates.py`。

## 巨鲸/聪明钱下一步实现建议

第一步不要直接写 UI，先确认 API contract：

- provider 名称。
- 鉴权方式。
- Base URL。
- 地址/对象 profile。
- 动作事件。
- 合约持仓。
- 现货持仓。
- 当前委托。
- 历史成交。
- 金额单位。
- 时间字段。
- 分页和限频。

推荐实现顺序：

1. 在后端定义 provider adapter interface。
2. 用 mock provider 写测试，证明数据能标准化。
3. 扩展 SQLite schema 或明确 `whale_events.payload_json` 的标准结构。
4. 实现后台巨鲸配置 CRUD。
5. 实现 worker 轮询和去重。
6. 接入首页巨鲸模块。
7. 接入二级详情页。
8. 增加通知模板和策略绑定。
9. 最后接真实 provider。

不要在真实 API 文档缺失时伪造“已接入”的能力。

## 已踩过的坑

- PowerShell 读取中文 Python 文件时可能显示乱码，不一定代表文件内容坏了。
- 系统 Python 可能缺 `pydantic` 等依赖，用项目 venv。
- `npm.cmd run build` 必须在 `frontend` 目录运行。
- `robocopy` 正常复制也可能返回非 0；`$LASTEXITCODE -le 7` 通常算成功。
- 后台 webhook 输入框曾因 React key 使用 id 导致输入失焦，后续改动不要恢复这个问题。
- 后台开关曾因 label 包整行导致误触，后续不要用整行 label 包开关。
- 浏览器自动化登录不稳定，必要时让用户手动登录后再验证。

## 完成任务前检查

代码类改动完成前至少检查：

- 前端构建是否通过。
- 相关后端测试是否通过。
- D 盘运行项目是否同步。
- 如果后端变更，服务是否重启。
- UI 是否和用户最新截图要求一致。
- 是否没有误触真实 webhook。
- 是否没有把巨鲸 UI 壳描述成真实功能。
