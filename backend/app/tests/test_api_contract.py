from __future__ import annotations

import os

os.environ["RUN_WORKERS"] = "false"

from fastapi.testclient import TestClient

from backend.app.core.security import make_admin_token
from backend.app.core.settings import load_runtime_settings
from backend.app.main import app


def test_public_snapshot_and_auth_guard() -> None:
    client = TestClient(app)
    snapshot = client.get("/api/snapshot")
    assert snapshot.status_code == 200
    assert any(item["symbol"] == "BTCUSDT" for item in snapshot.json()["symbols"])

    forbidden = client.put("/api/dashboard/modules", json=snapshot.json()["modules"])
    assert forbidden.status_code == 401


def test_strategy_put_with_admin_token() -> None:
    settings = load_runtime_settings()
    token = make_admin_token(settings.app_secret_key, settings.admin_password)
    client = TestClient(app)
    strategy = client.get("/api/strategies/kdj").json()
    response = client.put(
        "/api/strategies/kdj",
        json={"enabled": strategy["enabled"], "config": strategy["config"], "notifier_id": strategy["notifier_id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == "kdj"


def test_technical_notification_matrix_round_trip() -> None:
    settings = load_runtime_settings()
    token = make_admin_token(settings.app_secret_key, settings.admin_password)
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)
    strategy = client.get("/api/strategies/kdj").json()
    original_config = strategy["config"]
    partial_matrix = {"BTCUSDT": ["5m", "1h"], "ETHUSDT": ["4h"]}
    try:
        response = client.put(
            "/api/strategies/kdj",
            json={
                "enabled": strategy["enabled"],
                "config": {**original_config, "notify_intervals_by_symbol": partial_matrix},
                "notifier_id": strategy["notifier_id"],
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["config"]["notify_intervals_by_symbol"] == partial_matrix
        assert client.get("/api/strategies/kdj").json()["config"]["notify_intervals_by_symbol"] == partial_matrix
    finally:
        client.put(
            "/api/strategies/kdj",
            json={"enabled": strategy["enabled"], "config": original_config, "notifier_id": strategy["notifier_id"]},
            headers=headers,
        )


def test_cleanup_strategy_contract() -> None:
    settings = load_runtime_settings()
    token = make_admin_token(settings.app_secret_key, settings.admin_password)
    client = TestClient(app)
    strategy = client.get("/api/strategies/cleanup").json()
    assert strategy["id"] == "cleanup"
    assert "schedule_time" in strategy["config"]
    response = client.put(
        "/api/strategies/cleanup",
        json={"enabled": strategy["enabled"], "config": strategy["config"], "notifier_id": strategy["notifier_id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == "cleanup"
