# Market Monitor Dashboard

Full-stack MVP that integrates the crypto technical-alert service and Trump/White House statement monitor into one dashboard and admin panel.

## Stack

- Backend: FastAPI, SQLite, background polling workers
- Frontend: React, Vite, React Query, react-grid-layout, Lightweight Charts
- Storage: SQLite tables for strategy configs, symbols, dashboard layout, notifiers, alerts, news, source health, and app state

## Quick Start

```powershell
cd D:\market-monitor-dashboard

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

cd frontend
npm install
npm run build

cd ..
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8800
```

Then open `http://127.0.0.1:8800`.

Default admin password is controlled by `ADMIN_PASSWORD` and defaults to `change-me-admin`. Change it before real deployment.

The product UI, admin panel, strategy labels, and alert templates are Chinese-first. News translation is configured as an OpenAI-compatible large-model API from the admin panel.

## Linux Server Deployment

Use source code plus server-side builds; do not upload local `.venv`, `data`, `logs`, `node_modules`, or `frontend/dist`.

```bash
git clone https://github.com/kyrieljy/crypto-monitor.git
cd crypto-monitor

cp .env.example .env
vim .env

python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt

cd frontend
npm ci
npm run build
cd ..
```

Recommended persistent startup uses `systemd`, which survives terminal close, reboot, and process crashes:

```bash
sudo tee /etc/systemd/system/crypto-monitor.service >/dev/null <<'EOF'
[Unit]
Description=Crypto Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/crypto-monitor
EnvironmentFile=/opt/crypto-monitor/.env
Environment=RUN_WORKERS=true
ExecStart=/opt/crypto-monitor/.venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8800
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now crypto-monitor
sudo systemctl status crypto-monitor
sudo journalctl -u crypto-monitor -f
```

For quick testing, `nohup` also works, but it is weaker than `systemd` because it will not restart the service after a crash or reboot:

```bash
mkdir -p logs
RUN_WORKERS=true nohup .venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8800 > logs/app.log 2>&1 &
echo $! > crypto-monitor.pid
tail -f logs/app.log
```

`RUN_WORKERS=true` is required in production if you want technical strategies, news polling, robot notifications, and scheduled cleanup to run without opening the frontend page.

## Preserved And Added Strategy Scope

- Kept: KDJ J/K cross, MA fast/slow cross, Truthbrush, TrumpTruth RSS, White House gallery, classification, translation, dedupe, retry, cleanup.
- Removed: 1-minute range alert, 1-minute volume alert, GNews.
- Added: BOLL upper/lower band breakout on `1h` and `4h`.
- Added scaffold: Whale Watch provider/API configuration and events table-ready module. Real provider polling is intentionally disabled until the whale API contract is known.

## Dashboard Layout

- Row 1: five independent coin cards using default `15m` candles; each card groups that coin's KDJ, MA, and BOLL alerts separately.
- Row 2: Trump social alerts and White House news alerts.
- Row 3: Whale and smart-money watched address activity. Click an address to open a second-level detail page modeled after Binance positions, ready for positions, holdings, current operation amount, and order activity from a future whale API.
