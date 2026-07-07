# AGENTS

更新日期：2026-05-26

这是给下一位 Codex/开发代理的接力说明。开始任何开发前，先读：

1. `PROJECT_CONTEXT.md`
2. `ARCHITECTURE.md`
3. `TODO.md`
4. `DECISIONS.md`
5. 本文件

## 工作目录

主要项目目录：

```powershell
D:\market-monitor-dashboard
```

不要再把 `C:\Users\54901\Documents\Playground\market-monitor-dashboard` 当成当前主项目；当前实际项目和 Git 仓库在 D 盘。

服务器目录：

```bash
/root/crypto-monitor
```

GitHub：

```text
https://github.com/kyrieljy/crypto-monitor.git
```

## 工作方式

- 默认直接实现用户要求，不只给方案，除非用户明确只想讨论。
- 回答用户用中文。
- 搜索文件优先 `rg` / `rg --files`。
- 手动编辑文件用 `apply_patch`。
- 不要用 `git reset --hard`、`git checkout --` 回滚用户改动。
- 不要未确认就触发真实 webhook。
- 不要做交易、签名、钱包授权、跟单功能。
- 不要在清理策略里加本地备份。
- 不要把旧乱码文档内容继续复制到新文档里。

## 常用命令

后端测试：

```powershell
cd D:\market-monitor-dashboard
.\.venv\Scripts\python.exe -m pytest .\backend\app\tests -q
```

前端构建：

```powershell
cd D:\market-monitor-dashboard\frontend
npm.cmd run build
```

本地启动：

```powershell
cd D:\market-monitor-dashboard
.\.venv\Scripts\python.exe -m backend.app
```

Git 状态：

```powershell
cd D:\market-monitor-dashboard
git status --short
git log --oneline -5
```

## 服务器运维命令

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

依赖变化时：

```bash
cd /root/crypto-monitor/frontend
npm ci --include=dev
npm run build
```

日志：

```bash
sudo journalctl -u crypto-monitor -f
```

停止：

```bash
sudo systemctl stop crypto-monitor
```

启动：

```bash
sudo systemctl start crypto-monitor
```

重启：

```bash
sudo systemctl restart crypto-monitor
```

## 重要业务约束

### 机器人

- 只要 `RUN_WORKERS=true` 且服务启动，机器人推送不需要打开前端网页。
- 翻译和清理策略不绑定机器人。
- 技术告警机器人不显示 K 线时间。
- BOLL 机器人字段顺序是上轨、中轨、下轨。
- 巨鲸机器人只要关注对象有新操作就提醒。
- 巨鲸阈值只用于前台“大额”标记。
- 未经用户确认，不调用真实机器人测试。

### 社媒

- 不显示 RSS 原始 HTML。
- 图片/视频/转发帖要在系统内显示媒体预览。
- Truth Social 原帖跳 `https://truthsocial.com/@realDonaldTrump/posts/{id}`。
- 转发卡片也要翻译，优先显示 `metadata.card.translated_description`。
- 不显示翻译失败提示 token。
- 图片预览要完整缩小，不截取。

### 巨鲸

- 不做跟单按钮。
- 不做交易相关 API。
- 来源链接不是必填。
- 标签使用下拉多选，默认“聪明钱”。
- 麻吉大哥地址：`0x020ca66c30bec2c4fe3861a94e4db4a498a35872`。
- Hyperliquid SSL EOF 不一定是限频，但频率要保守。
- 首次同步不要补发历史成交通知。

### 前端偏好隔离

以下应该是终端/浏览器本地偏好，不应影响其他访问者：

- 暗色/亮色主题。
- 拖拽布局。
- 当前 K 线周期。
- KDJ/MA/BOLL 展示周期。
- ETH 大图指标模式。

以下可以是全局状态：

- 翻译结果。
- 后台策略配置。
- 机器人配置。
- 关注对象。
- 新闻 metadata 修复。

## 已踩过的坑

### Node 版本

服务器原来是 Node 12，会导致：

```text
SyntaxError: Unexpected token '?'
```

需要 Node 20+。

NodeSource 安装时可能和系统 `libnode-dev` 冲突，需要处理 apt/dpkg 冲突。

### HTTPS

当前没有 TLS。

正确访问：

```text
http://167.179.69.248:8800/
```

错误访问：

```text
https://167.179.69.248:8800/
```

### curl -I

`curl -I http://127.0.0.1:8800/` 可能返回 405，因为 root route 只支持 GET。用：

```bash
curl http://127.0.0.1:8800/
```

### VS Code Remote 路径

用户曾经在 `/opt/crypto-monitor` 找不到项目，因为 VS Code 左侧打开的是 `/root`。现在项目在：

```bash
/root/crypto-monitor
```

### needrestart / kernel dialog

apt 安装时出现 outdated libraries 或 pending kernel upgrade 对话框是系统提示。服务可以继续部署；是否重启服务器要单独安排。

### 前端黑屏

已出现过两类黑屏：

1. 浏览器缓存旧 `index.html` 指向旧 asset hash。
2. lightweight-charts 销毁时手动清空 DOM 导致 React removeChild 崩溃。

当前修复：

- `index.html` no-cache。
- `CoinChart` 不再 `container.innerHTML = ""`。
- 有 ErrorBoundary。

不要把上述问题改回去。

### Hyperliquid EOF

`SSLEOFError EOF occurred in violation of protocol` 多数时候是网络/TLS 断连，不一定是硬限频。当前仍使用保守轮询频率。

## 验证建议

文档改动：

- `git diff --check`

后端改动：

- 跑相关 pytest。
- 涉及通知模板时至少跑 `test_notification_templates.py` 和 `test_whale.py`。

前端改动：

- `npm.cmd run build`
- 用浏览器检查关键页面。
- 图表改动必须检查：
  - 非空白。
  - 刷新不黑屏。
  - 拖拽有效。
  - 滚轮缩放有效。
  - 指标切换不同时显示。

## 下一步推荐切入点

如果用户继续说“开始做下一步”，优先从 `TODO.md` 的 P0 取：

1. 生产同步验证。
2. 社媒转发历史翻译补齐。
3. 巨鲸最近成交/资金流水/历史订单分页和筛选。
4. EVM 大额转账监控方案与 Alchemy Free 评估。

如果用户说“推到服务器”或“上线”，直接给并执行常规更新流程，但不要擅自删除数据库或真实测试 webhook。
