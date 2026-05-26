from __future__ import annotations

import json
from pathlib import Path

from backend.app.api.schemas import WhaleTargetUpsert
from backend.app.core.database import Database
from backend.app.services.store import Store
from backend.app.services.whale import DeBankProvider, HyperliquidProvider, extract_addresses, resolve_address_candidates
from backend.app.services.whale_runner import WhaleRunner


def test_hyperliquid_position_fixture_is_normalized() -> None:
    payload = {
        "marginSummary": {"accountValue": "1500000.5", "totalMarginUsed": "593530.99", "totalNtlPos": "14838300"},
        "withdrawable": "900000",
        "assetPositions": [
            {
                "position": {
                    "coin": "ETH",
                    "szi": "6980.4181",
                    "entryPx": "2094.42",
                    "markPx": "2125.7",
                    "liquidationPx": "2078.2",
                    "marginUsed": "593530.99",
                    "positionValue": "14838300",
                    "unrealizedPnl": "218300",
                    "returnOnEquity": "0.3679",
                    "leverage": {"type": "cross", "value": 25},
                    "cumFunding": {"sinceOpen": "-13900"},
                }
            }
        ],
    }

    positions = HyperliquidProvider.parse_positions(payload)
    summary = HyperliquidProvider.parse_account_summary(payload)

    assert positions[0]["symbol"] == "ETH-USDC"
    assert positions[0]["side"] == "做多"
    assert positions[0]["leverage"] == 25
    assert positions[0]["entry_price"] == 2094.42
    assert positions[0]["mark_price"] == 2125.7
    assert positions[0]["liquidation_price"] == 2078.2
    assert positions[0]["pnl"] == 218300
    assert positions[0]["funding"] == -13900
    assert summary["account_value"] == 1500000.5


def test_hyperliquid_all_mids_backfills_mark_price_and_liquidation_distance() -> None:
    payload = {
        "assetPositions": [
            {
                "position": {
                    "coin": "ETH",
                    "szi": "2",
                    "entryPx": "2000",
                    "liquidationPx": "1800",
                    "positionValue": "0",
                    "unrealizedPnl": "0",
                    "returnOnEquity": "0",
                    "leverage": {"type": "cross", "value": 10},
                }
            }
        ],
    }

    mids = HyperliquidProvider.parse_market_mids({"ETH": "2100"})
    positions = HyperliquidProvider.parse_positions(payload, mids)
    summary = HyperliquidProvider.parse_account_summary({"withdrawable": "0"})

    assert positions[0]["mark_price"] == 2100
    assert positions[0]["notional"] == 4200
    assert round(positions[0]["liquidation_distance_pct"], 2) == 14.29
    assert summary["withdrawable"] == 0


def test_hyperliquid_fill_fixture_is_normalized() -> None:
    fills = HyperliquidProvider.parse_fills(
        [
            {
                "coin": "ETH",
                "px": "2100.5",
                "sz": "100",
                "side": "B",
                "dir": "Open Long",
                "closedPnl": "0",
                "hash": "0xabc",
                "oid": 123,
                "tid": 456,
                "fee": "12.5",
                "feeToken": "USDC",
                "time": 1779760800000,
            }
        ]
    )

    assert fills[0]["coin"] == "ETH"
    assert fills[0]["side"] == "买入"
    assert fills[0]["size"] == 100
    assert fills[0]["price"] == 2100.5
    assert fills[0]["notional"] == 210050
    assert fills[0]["fee"] == 12.5
    assert fills[0]["direction_label"] == "买入开多"
    assert fills[0]["price_label"] == "开仓价格"


def test_debank_fixtures_are_normalized() -> None:
    tokens = [
        {"symbol": "ETH", "name": "Ethereum", "chain": "eth", "amount": 2, "price": 3000},
        {"symbol": "DUST", "amount": 0, "price": 1},
    ]
    protocols = [
        {"name": "Aave", "chain": "eth", "net_usd_value": 1200, "portfolio_item_list": [{}, {}]},
    ]

    holdings = DeBankProvider.parse_holdings(tokens)
    defi = DeBankProvider.parse_protocols(protocols)

    assert holdings == [{"symbol": "ETH", "name": "Ethereum", "chain": "eth", "amount": 2.0, "price": 3000.0, "value": 6000.0, "logo_url": None}]
    assert defi[0]["name"] == "Aave"
    assert defi[0]["value"] == 1200
    assert defi[0]["item_count"] == 2


def test_address_resolver_extracts_links_and_local_candidates() -> None:
    address = "0x1111111111111111111111111111111111111111"
    assert extract_addresses(f"https://app.hyperliquid.xyz/explorer/address/{address}") == [address]

    local = [{"id": "machi", "label": "麻吉大哥", "address_or_subject": address, "config": {"addresses": [address]}}]
    direct = resolve_address_candidates(f"https://etherscan.io/address/{address}", [])
    by_name = resolve_address_candidates("麻吉", local)

    assert direct[0]["source"] == "etherscan_link"
    assert by_name[0]["target_id"] == "machi"


def test_store_upserts_whale_target_and_runner_generates_events(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    generated = store.upsert_whale_target(
        WhaleTargetUpsert(
            id=None,
            label="Smart Wallet",
            address_or_subject="0x1111111111111111111111111111111111111111",
            enabled=True,
            config={"tags": ["聪明钱"]},
        )
    )
    assert generated.id == "0x1111111111111111111111111111111111111111"
    assert generated.config["addresses"] == ["0x1111111111111111111111111111111111111111"]

    target = store.upsert_whale_target(
        WhaleTargetUpsert(
            id="smart-wallet",
            label="Smart Wallet",
            address_or_subject="0x2222222222222222222222222222222222222222",
            enabled=True,
            config={"tags": ["聪明钱"]},
        )
    )
    runner = WhaleRunner(store, bus=None)  # type: ignore[arg-type]
    old_snapshot = {
        "positions": [
            {"symbol": "ETH-USDC", "side": "做多", "notional": 100000, "liquidation_distance_pct": 8},
            {"symbol": "BTC-USDC", "side": "做空", "notional": 120000, "liquidation_distance_pct": 10},
        ]
    }
    new_snapshot = {
        "positions": [
            {"symbol": "ETH-USDC", "side": "做多", "notional": 150000, "liquidation_distance_pct": 3},
            {"symbol": "SOL-USDC", "side": "做多", "notional": 50000, "liquidation_distance_pct": 20},
        ]
    }

    runner._record_events(target.id, old_snapshot, new_snapshot, {"position_change_alert_pct": 25, "min_position_value_usd": 10000, "liquidation_distance_pct": 5})

    events = store.db.query("SELECT action_type FROM whale_events WHERE target_id = ? ORDER BY id", (target.id,))
    assert [row["action_type"] for row in events] == ["position_change", "liquidation_risk", "open_position", "close_position"]


def test_runner_records_every_fill_once_and_marks_notification(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    target = store.upsert_whale_target(
        WhaleTargetUpsert(
            id="smart-wallet",
            label="Smart Wallet",
            address_or_subject="0x2222222222222222222222222222222222222222",
            enabled=True,
            config={"tags": ["聪明钱"]},
        )
    )
    runner = WhaleRunner(store, bus=None)  # type: ignore[arg-type]
    fill = {
        "coin": "ETH",
        "side": "买入",
        "side_code": "B",
        "direction": "Open Long",
        "size": 0.5,
        "price": 2100,
        "notional": 1050,
        "fee": 0.4,
        "fee_token": "USDC",
        "hash": "0xfill",
        "timestamp": 1779760800000,
    }

    runner._record_fill_events(
        target,
        "0x2222222222222222222222222222222222222222",
        [fill],
        {"trade_min_notional_usd": 100000, "trade_coin_thresholds": {"ETH": 100}, "trade_notification_enabled": True},
        notify_allowed=True,
    )
    runner._record_fill_events(
        target,
        "0x2222222222222222222222222222222222222222",
        [fill],
        {"trade_min_notional_usd": 100000, "trade_coin_thresholds": {"ETH": 100}, "trade_notification_enabled": True},
        notify_allowed=True,
    )

    events = store.db.query("SELECT action_type, notification_required, notification_sent FROM whale_events WHERE target_id = ?", (target.id,))
    assert len(events) == 1
    assert events[0]["action_type"] == "trade_buy"
    assert events[0]["notification_required"] == 1
    assert events[0]["notification_sent"] == 0


def test_runner_adds_fill_position_context_from_current_and_old_snapshot(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    target = store.upsert_whale_target(
        WhaleTargetUpsert(
            id="smart-wallet",
            label="Smart Wallet",
            address_or_subject="0x2222222222222222222222222222222222222222",
            enabled=True,
            config={"tags": ["聪明钱"]},
        )
    )
    runner = WhaleRunner(store, bus=None)  # type: ignore[arg-type]
    fills = [
        {
            "coin": "ETH",
            "side": "买入",
            "side_code": "B",
            "direction": "Open Long",
            "size": 1,
            "price": 2100,
            "notional": 2100,
            "hash": "0xcurrent",
            "timestamp": 1779760800000,
            "raw": {"startPosition": "4.0", "side": "B"},
        },
        {
            "coin": "BTC",
            "side": "卖出",
            "side_code": "A",
            "direction": "Close Long",
            "size": 1,
            "price": 90000,
            "notional": 90000,
            "hash": "0xold",
            "timestamp": 1779760801000,
            "raw": {"startPosition": "3.0", "side": "A"},
        },
    ]

    runner._record_fill_events(
        target,
        "0x2222222222222222222222222222222222222222",
        fills,
        {"trade_notification_enabled": True},
        notify_allowed=True,
        current_positions=[{"coin": "ETH", "leverage": 25, "margin_mode": "全仓"}],
        old_positions=[{"symbol": "BTC-USDC", "leverage": 10, "margin_mode": "逐仓"}],
    )

    rows = store.db.query("SELECT payload_json FROM whale_events WHERE target_id = ? ORDER BY occurred_at_utc", (target.id,))
    payloads = [json.loads(row["payload_json"]) for row in rows]

    assert payloads[0]["direction_label"] == "买入开多"
    assert payloads[0]["price_label"] == "开仓价格"
    assert payloads[0]["current_position_size"] == 5
    assert payloads[0]["current_position_label"] == "5 ETH 多"
    assert payloads[0]["position_leverage"] == 25
    assert payloads[0]["position_margin_mode"] == "全仓"
    assert payloads[1]["direction_label"] == "卖出平多"
    assert payloads[1]["price_label"] == "平仓价格"
    assert payloads[1]["current_position_size"] == 2
    assert payloads[1]["current_position_label"] == "2 BTC 多"
    assert payloads[1]["position_leverage"] == 10
    assert payloads[1]["position_margin_mode"] == "逐仓"
