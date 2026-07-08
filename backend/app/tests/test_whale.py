from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from backend.app.api.schemas import WhaleTargetUpsert
from backend.app.core.database import Database
from backend.app.services.store import Store
from backend.app.services.whale import BlackRockFreeProvider, DeBankProvider, HyperliquidProvider, WhaleSnapshot, extract_addresses, extract_btc_addresses, resolve_address_candidates
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
            },
            {
                "coin": "ETH",
                "px": "2224",
                "sz": "98537.8279",
                "side": "A",
                "dir": "Liquidated Cross Long",
                "closedPnl": "-91164242",
                "time": 1779760801000,
            }
        ]
    )

    open_long = next(fill for fill in fills if fill["direction"] == "Open Long")
    liquidation = next(fill for fill in fills if fill["direction"] == "Liquidated Cross Long")

    assert open_long["coin"] == "ETH"
    assert open_long["side"] == "买入"
    assert open_long["size"] == 100
    assert open_long["price"] == 2100.5
    assert open_long["notional"] == 210050
    assert open_long["fee"] == 12.5
    assert open_long["direction_label"] == "买入开多"
    assert open_long["price_label"] == "开仓价格"
    assert liquidation["direction_label"] == "强平全仓多单"
    assert liquidation["price_label"] == "强平价格"


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


def test_blackrock_free_sources_are_normalized() -> None:
    ishares_html = """
    <div>Net Assets of Fund</div><div>$44,947,000,000</div><div>as of Jul 02, 2026</div>
    <div>Benchmark Level</div><div>$62,171.32</div>
    <div>Shares Outstanding</div><div>765,000,000</div>
    <div>Basket Bitcoin Amount</div><div>40.12</div>
    """
    farside_html = """
    <table>
      <tr><th>Total</th><th>IBIT</th><th>FBTC</th><th>BTC</th><th>Fee</th></tr>
      <tr><td>0.25</td><td>0.25</td><td>0.25</td><td>0.25</td></tr>
      <tr><td>02 Jul 2026</td><td>300.1</td><td>166.0</td><td>12.3</td><td>0.0</td></tr>
    </table>
    """

    official = BlackRockFreeProvider.parse_ishares_page(ishares_html)
    flow = BlackRockFreeProvider.parse_farside_flows(farside_html)
    escaped_ishares_html = """
    &quot;totalNetAssetsFundLevel&quot;:{&quot;label&quot;:&quot;Net Assets of Fund&quot;,&quot;formattedValue&quot;:&quot;44,947,412,010&quot;,&quot;value&quot;:&quot;44947412010.18&quot;,&quot;formattedAsOfDate&quot;:&quot;Jul 02, 2026&quot;},
    &quot;levelAmount&quot;:{&quot;label&quot;:&quot;Benchmark Level&quot;,&quot;formattedValue&quot;:&quot;62,171.32&quot;,&quot;formattedAsOfDate&quot;:&quot;Jul 03, 2026&quot;},
    &quot;sharesOutstanding&quot;:{&quot;label&quot;:&quot;Shares Outstanding&quot;,&quot;formattedValue&quot;:&quot;1,289,600,000&quot;,&quot;formattedAsOfDate&quot;:&quot;Jul 02, 2026&quot;},
    &quot;basketAmt&quot;:{&quot;label&quot;:&quot;Basket Bitcoin Amount&quot;,&quot;formattedValue&quot;:&quot;22.66&quot;,&quot;formattedAsOfDate&quot;:&quot;Jul 02, 2026&quot;},
    &quot;indicativeBasketAmt&quot;:{&quot;label&quot;:&quot;Indicative Basket Bitcoin Amount&quot;,&quot;formattedValue&quot;:&quot;22.64&quot;,&quot;formattedAsOfDate&quot;:&quot;Jul 02, 2026&quot;}
    """
    official_from_page_json = BlackRockFreeProvider.parse_ishares_page(escaped_ishares_html)

    assert official["net_assets"] == 44947000000
    assert official["benchmark_price"] == 62171.32
    assert round(official["estimated_btc_holdings"], 2) == round(44947000000 / 62171.32, 2)
    assert official_from_page_json["as_of"] == "Jul 02, 2026"
    assert official_from_page_json["net_assets"] == 44947412010
    assert official_from_page_json["benchmark_price"] == 62171.32
    assert official_from_page_json["shares_outstanding"] == 1289600000
    assert official_from_page_json["basket_btc"] == 22.66
    assert official_from_page_json["indicative_basket_btc"] == 22.64
    assert round(official_from_page_json["estimated_btc_holdings"], 2) == round(44947412010 / 62171.32, 2)
    assert flow["date"] == "02 Jul 2026"
    assert flow["ibit_flow_usd_m"] == 166.0


def test_blackrock_free_btc_cluster_transfer_parser() -> None:
    watched = {"bc1qblackrock0000000000000000000000000000000"}
    tx = {
        "txid": "btc-tx-1",
        "status": {"confirmed": True, "block_time": 1779760800},
        "vin": [
            {
                "prevout": {
                    "scriptpubkey_address": "bc1qblackrock0000000000000000000000000000000",
                    "value": 150_000_000_000,
                }
            }
        ],
        "vout": [
            {"scriptpubkey_address": "bc1qcoinbase00000000000000000000000000000000", "value": 120_000_000_000},
            {"scriptpubkey_address": "bc1qblackrock0000000000000000000000000000000", "value": 20_000_000_000},
        ],
    }

    transfer = BlackRockFreeProvider.parse_btc_cluster_transfer(tx, watched)

    assert transfer is not None
    assert transfer["txid"] == "btc-tx-1"
    assert transfer["direction"] == "out"
    assert transfer["amount_btc"] == 1300
    assert transfer["external_outputs"][0]["address"] == "bc1qcoinbase00000000000000000000000000000000"


def test_btc_address_operation_parser_tracks_each_address_action() -> None:
    address = "bc1qblackrock0000000000000000000000000000000"
    tx = {
        "txid": "btc-tx-2",
        "status": {"confirmed": True, "block_time": 1779760800},
        "vin": [{"prevout": {"scriptpubkey_address": address, "value": 150_000_000_000}}],
        "vout": [
            {"scriptpubkey_address": "bc1qcoinbase00000000000000000000000000000000", "value": 120_000_000_000},
            {"scriptpubkey_address": address, "value": 20_000_000_000},
        ],
    }

    operation = BlackRockFreeProvider.parse_btc_address_operation(tx, address)

    assert operation is not None
    assert operation["direction"] == "out"
    assert operation["behavior"] == "转出"
    assert operation["amount_btc"] == 1300
    assert operation["net_btc"] == -1300
    assert operation["output_counterparties"][0]["address"] == "bc1qcoinbase00000000000000000000000000000000"


def test_ibit_news_feed_candidates_extract_txid_addresses_and_amounts(monkeypatch) -> None:
    txid = "a" * 64
    source_address = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>贝莱德 IBIT 地址向 Coinbase 存入 4,917 枚 BTC</title>
      <link>https://example.com/news/ibit</link>
      <guid>ibit-news-1</guid>
      <pubDate>Mon, 06 Jul 2026 06:00:00 GMT</pubDate>
      <description>据 OnchainLens 监测，BlackRock IBIT 相关交易 txid {txid}，价值约 3.01 亿美元，地址 {source_address}</description>
    </item></channel></rss>"""
    provider = BlackRockFreeProvider()
    monkeypatch.setattr(provider, "_get_text", lambda url: rss)
    monkeypatch.setattr(
        provider,
        "_get_json",
        lambda path: {
            "txid": txid,
            "status": {"confirmed": True, "block_time": 1783317600},
            "vin": [{"prevout": {"scriptpubkey_address": source_address, "value": 491_700_000_000}}],
            "vout": [{"scriptpubkey_address": "1BoatSLRHtKNngkdXEeobR76b53LETtpyT", "value": 491_700_000_000}],
        },
    )

    signals = provider.fetch_news_signals(["https://example.com/rss"], keywords=["IBIT", "贝莱德"], lookback_hours=24 * 30, max_items=10)

    assert len(signals) == 1
    signal = signals[0]
    assert signal["txids"] == [txid]
    assert source_address in signal["candidate_addresses"]
    assert signal["btc_amounts"] == [4917]
    assert signal["usd_amounts"] == [301_000_000]
    assert signal["mentions_coinbase"] is True
    assert signal["confidence"] >= 0.8
    assert signal["tx_candidates"][0]["input_addresses"] == [source_address]

    suspected = BlackRockFreeProvider.build_suspected_addresses(signals)
    assert suspected[0]["address"] == source_address
    assert suspected[0]["confidence"] == signal["confidence"]
    assert suspected[0]["signals"][0]["behavior"] == "新闻称向 Coinbase 存入/转入"
    assert suspected[0]["txids"] == [txid]


def test_ibit_news_feed_extracts_eth_amounts_and_evm_addresses(monkeypatch) -> None:
    evm_address = "0x1111111111111111111111111111111111111111"
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>BlackRock associated wallet moved 2,700 BTC and 52,956 ETH to Coinbase</title>
      <link>https://example.com/news/blackrock-eth</link>
      <guid>blackrock-eth-news-1</guid>
      <pubDate>Mon, 06 Jul 2026 06:00:00 GMT</pubDate>
      <description>OnchainLens reported the BlackRock related address {evm_address} transferred 52,956 ETH to Coinbase.</description>
    </item></channel></rss>"""
    provider = BlackRockFreeProvider()
    monkeypatch.setattr(provider, "_get_text", lambda url: rss)

    signals = provider.fetch_news_signals(["https://example.com/rss"], keywords=["BlackRock", "ETH"], lookback_hours=24 * 30, max_items=10)

    assert len(signals) == 1
    signal = signals[0]
    assert signal["btc_amounts"] == [2700]
    assert signal["eth_amounts"] == [52956]
    assert signal["evm_addresses"] == [evm_address]
    assert evm_address in signal["candidate_addresses"]
    assert signal["mentions_coinbase"] is True
    assert signal["mentions_blackrock_or_ibit"] is True


def test_suspected_address_pool_matches_news_by_similar_operation() -> None:
    address = "bc1qcandidate0000000000000000000000000000000"
    unrelated_address = "bc1qunrelated000000000000000000000000000000"
    signal = {
        "id": "blockbeats-onchainlens-2026-07-02",
        "title": "贝莱德ETF地址向Coinbase存入4917枚BTC，价值约3.01亿美元",
        "summary": "BlockBeats 消息，7月2日，据 OnchainLens 监测，贝莱德 ETF 地址向 Coinbase 存入 4,917 枚 BTC，价值约 3.01 亿美元。近4天贝莱德已累计向 Coinbase 存入 20,359 枚 BTC。",
        "published_at": "2026-07-02T11:22:00+00:00",
        "confidence": 0.6,
        "candidate_addresses": [],
        "txids": [],
        "btc_amounts": [4917],
        "usd_amounts": [301_000_000],
        "mentions_coinbase": True,
    }
    matching_operation = {
        "address": address,
        "txid": "btc-tx-match",
        "direction": "out",
        "behavior": "转出",
        "amount_btc": 4917,
        "net_btc": -4917,
        "timestamp": "2026-07-02T11:10:00+00:00",
        "timestamp_ms": 1782990600000,
    }
    unrelated_operation = {
        "address": unrelated_address,
        "txid": "btc-tx-unrelated",
        "direction": "in",
        "behavior": "转入",
        "amount_btc": 120,
        "net_btc": 120,
        "timestamp": "2026-07-01T00:00:00+00:00",
        "timestamp_ms": 1782864000000,
    }

    suspected = BlackRockFreeProvider.build_suspected_addresses(
        [signal],
        candidate_activity={
            address: [matching_operation],
            unrelated_address: [unrelated_operation],
        },
    )

    assert suspected[0]["address"] == address
    assert suspected[0]["confidence"] >= 0.7
    assert suspected[0]["latest_operations"][0]["txid"] == "btc-tx-match"
    assert suspected[0]["signals"][0]["operation"]["txid"] == "btc-tx-match"
    assert "BTC 数量接近新闻金额" in suspected[0]["reasons"]
    assert "链上转出方向与新闻行为一致" in suspected[0]["reasons"]
    assert suspected[1]["address"] == unrelated_address
    assert suspected[1]["confidence"] == 0.1


def test_address_resolver_extracts_links_and_local_candidates() -> None:
    address = "0x1111111111111111111111111111111111111111"
    assert extract_addresses(f"https://app.hyperliquid.xyz/explorer/address/{address}") == [address]

    local = [{"id": "machi", "label": "麻吉大哥", "address_or_subject": address, "config": {"addresses": [address]}}]
    direct = resolve_address_candidates(f"https://etherscan.io/address/{address}", [])
    by_name = resolve_address_candidates("麻吉", local)

    assert direct[0]["source"] == "etherscan_link"
    assert by_name[0]["target_id"] == "machi"


def test_extract_btc_addresses() -> None:
    assert extract_btc_addresses("watch bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080 now") == ["bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"]


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


def test_sync_target_now_forces_extended_snapshot(monkeypatch, tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    strategy = store.get_strategy("whale")
    assert strategy is not None
    config = dict(strategy.config)
    config["enabled"] = True
    store.update_strategy("whale", True, config, None)
    target = store.upsert_whale_target(
        WhaleTargetUpsert(
            id="instant-whale",
            label="Instant Whale",
            address_or_subject="0x2222222222222222222222222222222222222222",
            enabled=True,
            config={},
        )
    )
    include_extended_calls: list[bool] = []

    class FakeHyperliquidProvider:
        source_name = "hyperliquid"
        label = "Hyperliquid"

        def __init__(self, base_url: str, timeout_seconds: int) -> None:
            pass

        def fetch(self, address: str, *, include_extended: bool = True) -> WhaleSnapshot:
            include_extended_calls.append(include_extended)
            return WhaleSnapshot(
                positions=[{"coin": "ETH", "symbol": "ETH-USDC", "side": "做多", "size": 1, "notional": 2200}],
                fills=[],
                historical_orders=[{"coin": "ETH", "size": 1}],
                funding=[{"coin": "ETH", "amount": -1}],
                account_summary={"account_value": 10000},
            )

    monkeypatch.setattr("backend.app.services.whale_runner.HyperliquidProvider", FakeHyperliquidProvider)

    WhaleRunner(store, bus=None).sync_target_now(target.id, force_extended=True)  # type: ignore[arg-type]

    snapshot = store.get_whale_snapshot(target.id)
    assert include_extended_calls == [True]
    assert snapshot["positions"][0]["coin"] == "ETH"
    assert snapshot["historical_orders"][0]["coin"] == "ETH"
    assert snapshot["funding"][0]["amount"] == -1


def test_blackrock_free_target_sync_records_btc_event_once(monkeypatch, tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    strategy = store.get_strategy("whale")
    assert strategy is not None
    config = dict(strategy.config)
    config.update(
        {
            "enabled": True,
            "blackrock_free_enabled": True,
            "blackrock_free_notification_enabled": True,
            "blackrock_btc_transfer_min_btc": 1000,
        }
    )
    store.update_strategy("whale", True, config, None)
    target = store.upsert_whale_target(
        WhaleTargetUpsert(
            id="blackrock-free",
            label="IBIT 免费监控",
            address_or_subject="IBIT",
            enabled=True,
            config={
                "provider": "blackrock_free_monitor",
                "btc_addresses": ["bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"],
            },
        )
    )

    class FakeBlackRockFreeProvider:
        source_name = "blackrock_free"
        label = "IBIT Free Monitor"

        def __init__(self, **kwargs):  # noqa: ANN001
            pass

        def fetch(self, *, btc_addresses, transfer_min_btc, transfer_lookback_hours, **kwargs):  # noqa: ANN001
            assert btc_addresses == ["bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"]
            assert transfer_min_btc == 1000
            return WhaleSnapshot(
                holdings=[{"symbol": "IBIT-BTC", "value": 44_947_000_000}],
                account_summary={
                    "source": "blackrock_free",
                    "blackrock_official_net_assets": 44_947_000_000,
                    "blackrock_last_flow_date": "02 Jul 2026",
                    "blackrock_last_flow_usd": 166_000_000,
                    "blackrock_btc_cluster_address_count": 1,
                },
                source_status={"ishares": {"ok": True}, "farside": {"ok": True}, "btc_cluster": {"ok": True}},
                raw={
                    "btc_cluster": {
                        "transfers": [
                            {
                                "txid": "btc-tx-1",
                                "amount_btc": 1300,
                                "timestamp_ms": 1779760800000,
                                "source_url": "https://blockstream.info/tx/btc-tx-1",
                            }
                        ]
                    }
                },
            )

    monkeypatch.setattr("backend.app.services.whale_runner.BlackRockFreeProvider", FakeBlackRockFreeProvider)

    runner = WhaleRunner(store, bus=None)  # type: ignore[arg-type]
    runner.sync_target_now(target.id, force_extended=True)
    runner.sync_target_now(target.id, force_extended=True)

    events = store.db.query("SELECT provider, action_type, notification_required, summary FROM whale_events WHERE target_id = ?", (target.id,))
    assert len(events) == 1
    assert events[0]["provider"] == "blackrock_free"
    assert events[0]["action_type"] == "blackrock_confirmed_btc_outflow"
    assert events[0]["notification_required"] == 0
    assert "1300 BTC" in events[0]["summary"]
    snapshot = store.get_whale_snapshot(target.id)
    assert snapshot["account_summary"]["blackrock_last_flow_usd"] == 166_000_000
    listed = next(item for item in store.list_whale_targets() if item.id == target.id)
    assert listed.config["current_operation_amount"] == 166_000_000


def test_ibit_news_candidate_event_is_recorded_once(monkeypatch, tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    strategy = store.get_strategy("whale")
    assert strategy is not None
    config = dict(strategy.config)
    config.update(
        {
            "enabled": True,
            "blackrock_free_enabled": True,
            "blackrock_free_notification_enabled": True,
            "ibit_news_enabled": True,
            "ibit_news_candidate_notify_min_confidence": 0.6,
            "ibit_news_rss_urls": ["https://example.com/rss"],
        }
    )
    store.update_strategy("whale", True, config, None)
    target = store.upsert_whale_target(
        WhaleTargetUpsert(
            id="ibit-free",
            label="IBIT 免费监控",
            address_or_subject="IBIT",
            enabled=True,
            config={"provider": "blackrock_free_monitor"},
        )
    )
    store.save_whale_snapshot(
        target.id,
        {
            "account_summary": {"source": "blackrock_free", "ibit_news_candidate_count": 1},
            "raw": {"news_signals": {"signals": [{"id": "old-news", "title": "旧新闻线索"}]}},
        },
    )

    class FakeBlackRockFreeProvider:
        source_name = "blackrock_free"
        label = "IBIT Free Monitor"

        def __init__(self, **kwargs):  # noqa: ANN001
            pass

        def fetch(self, **kwargs):  # noqa: ANN001
            assert kwargs["news_enabled"] is True
            assert kwargs["news_feed_urls"] == ["https://example.com/rss"]
            return WhaleSnapshot(
                account_summary={"source": "blackrock_free", "ibit_news_candidate_count": 1},
                source_status={"news_signals": {"ok": True, "signals": 1}},
                raw={
                    "news_signals": {
                        "signals": [
                            {
                                "id": "news-1",
                                "title": "IBIT 地址向 Coinbase 存入 BTC",
                                "published_at": "2026-07-06T06:00:00+00:00",
                                "confidence": 0.85,
                                "candidate_addresses": ["bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"],
                                "txids": ["a" * 64],
                                "reasons": ["包含 txid", "提到 Coinbase"],
                                "url": "https://example.com/news/1",
                            }
                        ]
                    }
                },
            )

    monkeypatch.setattr("backend.app.services.whale_runner.BlackRockFreeProvider", FakeBlackRockFreeProvider)

    runner = WhaleRunner(store, bus=None)  # type: ignore[arg-type]
    runner.sync_target_now(target.id, force_extended=True)
    runner.sync_target_now(target.id, force_extended=True)

    events = store.db.query("SELECT action_type, notification_required, summary FROM whale_events WHERE target_id = ?", (target.id,))
    assert len(events) == 1
    assert events[0]["action_type"] == "ibit_news_address_candidate"
    assert events[0]["notification_required"] == 1
    assert "新闻线索" in events[0]["summary"]


def test_blackrock_ibit_notification_switches_are_independent(tmp_path: Path) -> None:
    event_switches = {
        "blackrock_etf_flow": "blackrock_etf_flow_notification_enabled",
        "blackrock_confirmed_btc_outflow": "blackrock_btc_outflow_notification_enabled",
        "blackrock_btc_address_operation": "blackrock_btc_address_operation_notification_enabled",
        "ibit_news_address_candidate": "ibit_news_candidate_notification_enabled",
    }

    def record_with(enabled_key: str | None) -> dict[str, int]:
        store = Store(Database(tmp_path / f"{enabled_key or 'none'}.db", "secret"))
        runner = WhaleRunner(store, bus=None)  # type: ignore[arg-type]
        target = SimpleNamespace(id="ibit-free", label="IBIT 免费监控")
        base_config = {
            "blackrock_free_notification_enabled": True,
            "blackrock_etf_flow_notification_enabled": False,
            "blackrock_btc_outflow_notification_enabled": False,
            "blackrock_btc_address_operation_notification_enabled": False,
            "ibit_news_candidate_notification_enabled": False,
            "ibit_news_candidate_notify_min_confidence": 0.6,
            "blackrock_initial_notification_enabled": True,
            "ibit_news_initial_notification_enabled": True,
        }
        if enabled_key:
            base_config[enabled_key] = True
        old_snapshot = {
            "account_summary": {"blackrock_last_flow_date": "01 Jul 2026"},
            "raw": {"news_signals": {"signals": [{"id": "old-news"}]}},
        }
        new_snapshot = {
            "account_summary": {
                "blackrock_last_flow_date": "02 Jul 2026",
                "blackrock_last_flow_usd": 166_000_000,
            },
            "raw": {
                "btc_cluster": {
                    "transfers": [
                        {
                            "txid": "btc-transfer-1",
                            "amount_btc": 1300,
                            "timestamp_ms": 1779760800000,
                        }
                    ],
                    "address_activity": {
                        "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080": [
                            {
                                "txid": "btc-operation-1",
                                "direction": "in",
                                "behavior": "转入",
                                "amount_btc": 300,
                                "net_btc": 300,
                                "timestamp_ms": 1779760860000,
                            }
                        ]
                    },
                },
                "news_signals": {
                    "signals": [
                        {
                            "id": "news-1",
                            "title": "IBIT 地址向 Coinbase 存入 BTC",
                            "published_at": "2026-07-06T06:00:00+00:00",
                            "confidence": 0.85,
                            "candidate_addresses": ["bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"],
                        }
                    ]
                },
            },
        }
        runner._record_blackrock_free_events(target, old_snapshot, new_snapshot, base_config, initial=False)  # noqa: SLF001
        rows = store.db.query("SELECT action_type, notification_required FROM whale_events WHERE target_id = ?", (target.id,))
        return {row["action_type"]: int(row["notification_required"]) for row in rows}

    all_disabled = record_with(None)
    assert set(all_disabled) == set(event_switches)
    assert all(value == 0 for value in all_disabled.values())

    for event_type, switch_key in event_switches.items():
        result = record_with(switch_key)
        assert result[event_type] == 1
        for other_type in set(event_switches) - {event_type}:
            assert result[other_type] == 0


def test_ibit_notification_switches_persist_false_after_strategy_save(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    strategy = store.get_strategy("whale")
    assert strategy is not None
    config = dict(strategy.config)
    config.update(
        {
            "blackrock_free_enabled": True,
            "blackrock_btc_address_operation_notification_enabled": False,
            "blackrock_btc_outflow_notification_enabled": False,
            "ibit_news_candidate_notification_enabled": False,
            "blackrock_etf_flow_notification_enabled": False,
        }
    )

    saved = store.update_strategy("whale", True, config, strategy.notifier_id)
    reloaded = store.get_strategy("whale")

    assert saved.config["blackrock_btc_address_operation_notification_enabled"] is False
    assert saved.config["blackrock_btc_outflow_notification_enabled"] is False
    assert saved.config["ibit_news_candidate_notification_enabled"] is False
    assert saved.config["blackrock_etf_flow_notification_enabled"] is False
    assert reloaded is not None
    assert reloaded.config["blackrock_btc_address_operation_notification_enabled"] is False
    assert reloaded.config["blackrock_btc_outflow_notification_enabled"] is False
    assert reloaded.config["ibit_news_candidate_notification_enabled"] is False
    assert reloaded.config["blackrock_etf_flow_notification_enabled"] is False


def test_blackrock_detail_rehydrates_address_operations_from_bottom_table(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    btc_address = "36YZXcTVLPdyapYuqXdJEt46oMVB2NrzVv"
    eth_address = "0x1111111111111111111111111111111111111111"
    now = datetime.now(timezone.utc).isoformat()
    target = store.upsert_whale_target(
        WhaleTargetUpsert(
            id="ibit-free",
            label="IBIT Free",
            address_or_subject="IBIT",
            enabled=True,
            config={"provider": "blackrock_free_monitor", "btc_addresses": [btc_address, eth_address]},
        )
    )
    store.save_whale_snapshot(
        target.id,
        {
            "account_summary": {"source": "blackrock_free", "blackrock_btc_cluster_operation_count": 0},
            "raw": {"btc_cluster": {"address_activity": {}}, "news_signals": {}},
        },
    )
    store.upsert_btc_large_transfer(
        {
            "txid": "btc-rehydrated-1",
            "chain": "btc",
            "asset": "BTC",
            "block_height": 956900,
            "block_hash": "btc-block",
            "block_time_utc": now,
            "amount": 2265.6855,
            "amount_btc": 2265.6855,
            "total_input_amount": 2265.6855,
            "total_output_amount": 2265.6855,
            "fee_amount": 0,
            "total_input_btc": 2265.6855,
            "total_output_btc": 2265.6855,
            "fee_btc": 0,
            "input_addresses": [{"address": btc_address, "value": 2265.6855, "value_btc": 2265.6855}],
            "output_addresses": [{"address": "bc1counterparty000000000000000000000000000000", "value": 2265.6855, "value_btc": 2265.6855}],
            "address_operations": [{"address": btc_address, "direction": "out", "behavior": "transfer_out", "amount": 2265.6855, "amount_btc": 2265.6855, "net_btc": -2265.6855}],
            "exchange_hints": [],
            "source_url": "https://blockstream.info/tx/btc-rehydrated-1",
            "raw": {"source": "test"},
        }
    )
    store.upsert_btc_large_transfer(
        {
            "txid": "eth-rehydrated-1",
            "chain": "eth",
            "asset": "ETH",
            "block_height": 123456,
            "block_hash": "eth-block",
            "block_time_utc": now,
            "amount": 7546,
            "amount_btc": 0,
            "amount_eth": 7546,
            "total_input_amount": 7546,
            "total_output_amount": 7546,
            "fee_amount": 0,
            "total_input_btc": 0,
            "total_output_btc": 0,
            "fee_btc": 0,
            "input_addresses": [{"address": eth_address, "value": 7546, "value_eth": 7546}],
            "output_addresses": [{"address": "0x2222222222222222222222222222222222222222", "value": 7546, "value_eth": 7546}],
            "address_operations": [{"address": eth_address, "direction": "out", "behavior": "transfer_out", "amount": 7546, "amount_eth": 7546, "net_eth": -7546}],
            "exchange_hints": [],
            "source_url": "https://etherscan.io/tx/eth-rehydrated-1",
            "raw": {"source": "test"},
        }
    )
    suspected_address = "0x3333333333333333333333333333333333333333"
    signal = {
        "id": "news-rehydrated-1",
        "title": "BlackRock moved ETH to Coinbase",
        "url": "https://example.com/news-rehydrated-1",
        "published_at": now,
        "confidence": 0.6,
        "candidate_addresses": [],
        "eth_amounts": [7546],
        "usd_amounts": [13_200_000],
        "large_transfer_matches": [
            {
                "txid": "eth-rehydrated-1",
                "chain": "eth",
                "asset": "ETH",
                "candidate_address": suspected_address,
                "address_role": "source",
                "address_value": 7546,
                "address_value_eth": 7546,
                "confidence": 0.85,
                "reasons": ["链上交易与新闻时间相近"],
                "source_url": "https://etherscan.io/tx/eth-rehydrated-1",
                "transfer": {
                    "txid": "eth-rehydrated-1",
                    "chain": "eth",
                    "asset": "ETH",
                    "block_time_utc": now,
                    "amount": 7546,
                    "source_url": "https://etherscan.io/tx/eth-rehydrated-1",
                },
            }
        ],
    }
    assert store.save_btc_news_matches(target.id, [signal]) == 1

    detail = store.get_whale_detail(target.id)

    assert detail is not None
    activity = detail.snapshot["raw"]["btc_cluster"]["address_activity"]
    assert activity[btc_address][0]["txid"] == "btc-rehydrated-1"
    assert activity[eth_address][0]["txid"] == "eth-rehydrated-1"
    assert activity[eth_address][0]["asset"] == "ETH"
    assert detail.account_summary["blackrock_btc_cluster_operation_count"] == 2
    news = detail.snapshot["raw"]["news_signals"]
    assert news["signals"][0]["id"] == "news-rehydrated-1"
    assert news["signals"][0]["large_transfer_matches"][0]["candidate_address"] == suspected_address
    suspected = {item["address"]: item for item in news["suspected_addresses"]}
    assert suspected_address in suspected
    assert suspected[suspected_address]["signals"][0]["id"] == "news-rehydrated-1"
    assert detail.account_summary["ibit_news_candidate_count"] == 1
    assert detail.account_summary["ibit_suspected_address_count"] >= 1
