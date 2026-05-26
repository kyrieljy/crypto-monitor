# 待办事项

## 下一阶段重点：巨鲸与聪明钱

用户下一步要重新开窗口做巨鲸和聪明钱功能。当前只是 UI 壳，不能当成真实监控已完成。

优先级 P0：

- 确认巨鲸/聪明钱 API 文档：
  - 鉴权方式。
  - Base URL。
  - 地址/对象查询接口。
  - 动作事件接口。
  - 持仓/现货/委托/成交接口。
  - 分页、限频、时间字段、金额币种。
- 定义 provider adapter：
  - `list_targets` 或 `get_target_profile`
  - `fetch_events(target, since)`
  - `fetch_positions(target)`
  - `fetch_holdings(target)`
  - `fetch_orders(target)`
  - 错误类型和限频处理。
- 扩展后台配置：
  - provider 类型。
  - API Base URL。
  - API Key。
  - 轮询间隔。
  - 关注地址/对象 CRUD。
  - 地址标签。
  - 启停。
  - 绑定通知机器人。
- 实现巨鲸 worker：
  - 定时轮询 provider。
  - 去重入库。
  - 更新 `source_health`。
  - 写入 `whale_events`。
  - 更新地址配置里的 positions/holdings，或拆出独立 snapshot 表。
- 把首页巨鲸模块接到真实数据：
  - 最近动作。
  - 当前操作金额。
  - 当前持仓。
  - 盈亏/仓位价值。
- 把二级详情页接到真实数据：
  - 基本信息。
  - 合约持仓。
  - 现货持仓。
  - 当前委托。
  - 历史动作。
- 增加巨鲸通知模板：
  - 大额买入/卖出。
  - 开仓/平仓。
  - 加仓/减仓。
  - 清仓。
  - 资金转入/转出。
- 增加测试：
  - provider mock。
  - 去重。
  - 轮询状态。
  - 持仓渲染。
  - 通知模板。

## 技术策略后续

- 补齐 KDJ/MA/BOLL 的完整单元测试覆盖：
  - 上穿/下穿边界。
  - live candle 开关。
  - 参数热更新。
  - 数据源灾备。
  - 去重。
- 后台策略表单继续细化：
  - 输入范围校验。
  - 保存前中文错误提示。
  - 数据源选择说明。
- 明确 MA 多周期配置的 UI 和后端字段是否完全统一，目前历史上有 `interval` 和 `intervals` 两种字段，需要保持兼容。

## 新闻和翻译后续

- 验证 Truthbrush 关闭、RSS 开启时是否完全避免重复。
- 白宫新闻需要继续观察实际抓取数据：
  - Gallery 结构是否变化。
  - include/exclude keywords 是否过严。
  - 源站网络失败时中文错误是否准确。
- 大模型翻译：
  - 确认 `deepseek-v4-flash` 在当前 API 地址可用。
  - 增加翻译失败时的中文错误原因。
  - 避免重复翻译已经中文的新闻。
  - 增加批量翻译进度状态。

## Webhook 和通知后续

- 在用户确认后，使用真实飞书 webhook 做一次端到端测试。
- 增加测试发送结果展示：
  - HTTP 状态码。
  - 飞书返回 code/message。
  - 网络错误中文原因。
- 增加 Telegram 类型的真实测试。
- 机器人绑定 UI 可以进一步明确：
  - 每个策略默认机器人。
  - 禁用策略时是否仍允许测试机器人。
  - 通知失败重试次数和间隔。

## 前端后续

- 拆分 `App.tsx`：
  - `DashboardPage`
  - `AdminPage`
  - `MarketStrategyPanel`
  - `NewsPanel`
  - `WhalePanel`
  - `WhaleDetailPage`
  - `NotifierEditor`
- 增加前端测试：
  - 登录保护。
  - 模块显示开关。
  - 策略周期切换。
  - “更多/收起”高度自适应。
  - 新闻翻译按钮置灰。
  - webhook 编辑输入不失焦。
- 移动端继续检查：
  - 五币行情卡片。
  - 策略卡片文字不截断。
  - 后台表单布局。

## 运维和部署后续

- 增加 `.env` 说明：
  - `DATABASE_PATH`
  - `HOST`
  - `PORT`
  - `APP_SECRET_KEY`
  - `ADMIN_PASSWORD`
  - `RUN_WORKERS`
  - `REQUEST_TIMEOUT_SECONDS`
- 生产部署前必须更换：
  - `APP_SECRET_KEY`
  - `ADMIN_PASSWORD`
  - 所有真实 webhook/token。
- 增加日志轮转。
- 增加数据库备份。
- 增加健康检查页面或接口聚合。

## 已完成清单

- 建立新项目而不是直接复用旧项目为主项目。
- 后端 FastAPI + SQLite。
- 前端 React + Vite。
- KDJ、MA、BOLL 策略基础实现。
- Trump RSS、Truthbrush 可选、White House 新闻源。
- 新闻分类、去重、翻译骨架。
- 后台登录保护。
- 策略配置、币种配置、Webhook 机器人配置、模块显示配置。
- 首页行情与策略监控按币种分列、按策略分组。
- 看板布局按用户要求调整到 4 行结构。
- 机器人推送模板和前台看板短文案分离。
- 巨鲸/聪明钱 UI 壳和二级详情页。
- 巨鲸返回恢复滚动位置。
- 后台提示弹框居中。
- 后台开关误触发修复。
- 数据源健康中文错误解析。

## 当前不做或暂缓

- 不做 GNews。
- 不恢复 1 分钟高低差告警。
- 不恢复 1 分钟成交量告警。
- 不在没有 API 文档时伪造巨鲸真实监控。
- 不把 KDJ、MA、BOLL 作为独立模块重新放回模块显示列表。
- 不让行情与策略监控模块恢复拖拽。
