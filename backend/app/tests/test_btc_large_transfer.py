from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.app.core.database import Database
from backend.app.services.btc_large_transfer import match_news_signal_to_large_transfers, parse_btc_large_transfer
from backend.app.services.eth_large_transfer import EthLargeTransferProvider, parse_eth_large_transfer
from backend.app.services.store import Store


def _sample_tx(value_sat: int = 491_700_000_000, block_time: int = 1783000000) -> dict:
    return {
        "txid": "a" * 64,
        "fee": 10_000,
        "status": {"confirmed": True, "block_height": 900001, "block_hash": "block-hash", "block_time": block_time},
        "vin": [
            {"prevout": {"scriptpubkey_address": "bc1source111111111111111111111111111111111", "value": value_sat + 10_000_000}},
        ],
        "vout": [
            {"scriptpubkey_address": "bc1coinbaseDeposit111111111111111111111", "value": value_sat},
            {"scriptpubkey_address": "bc1change111111111111111111111111111111111", "value": 9_990_000},
        ],
    }


def test_parse_btc_large_transfer_extracts_addresses_and_amounts() -> None:
    transfer = parse_btc_large_transfer(_sample_tx(), block_height=900001, block_hash="block-hash", block_timestamp=1783000000, min_btc=500)

    assert transfer is not None
    assert transfer["txid"] == "a" * 64
    assert transfer["amount_btc"] == 4917
    assert transfer["input_addresses"][0]["address"].startswith("bc1source")
    assert transfer["output_addresses"][0]["address"].startswith("bc1coinbaseDeposit")
    assert transfer["address_operations"][0]["amount_btc"] >= 4917


def test_parse_btc_large_transfer_skips_below_threshold() -> None:
    transfer = parse_btc_large_transfer(_sample_tx(100_000_000), block_height=900001, block_hash="block-hash", block_timestamp=1783000000, min_btc=500)

    assert transfer is None


def test_btc_large_transfer_store_dedupes_txid(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    transfer = parse_btc_large_transfer(_sample_tx(), block_height=900001, block_hash="block-hash", block_timestamp=1783000000, min_btc=500)
    assert transfer is not None

    assert store.upsert_btc_large_transfer(transfer) is True
    assert store.upsert_btc_large_transfer(transfer) is False
    rows = store.list_btc_large_transfers(limit=10)

    assert rows.total == 1
    assert rows.items[0].txid == "a" * 64
    assert rows.items[0].chain == "btc"
    assert rows.items[0].asset == "BTC"
    assert rows.items[0].amount == 4917


def test_btc_news_match_upsert_refreshes_signal_snapshot(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    transfer = parse_btc_large_transfer(_sample_tx(), block_height=900001, block_hash="block-hash", block_timestamp=1783000000, min_btc=500)
    assert transfer is not None
    store.upsert_btc_large_transfer(transfer)
    match = {
        "txid": transfer["txid"],
        "candidate_address": "bc1source111111111111111111111111111111111",
        "address_role": "source",
        "confidence": 0.72,
        "reasons": ["旧匹配理由"],
        "transfer": transfer,
    }
    signal = {
        "id": "blackrock-news",
        "title": "贝莱德链上转账",
        "summary": "旧摘要",
        "original_text": "旧原文",
        "published_at": datetime.fromtimestamp(1783000000, tz=timezone.utc).isoformat(),
        "large_transfer_matches": [match],
    }

    assert store.save_btc_news_matches("ibit-free", [signal]) == 1
    signal["summary"] = "新摘要"
    signal["original_text"] = "新原文"
    signal["large_transfer_matches"] = [{**match, "confidence": 0.91, "reasons": ["新匹配理由"]}]
    store.save_btc_news_matches("ibit-free", [signal])

    saved = store.get_btc_large_transfer(str(transfer["txid"]))
    assert saved is not None
    assert len(saved.matches) == 1
    assert saved.matches[0]["confidence"] == 0.91
    assert saved.matches[0]["reasons"] == ["新匹配理由"]
    assert saved.matches[0]["signal"]["original_text"] == "新原文"


def test_parse_eth_large_transfer_extracts_native_transfer() -> None:
    transfer = parse_eth_large_transfer(
        {
            "hash": "0x" + "b" * 64,
            "from": "0x1111111111111111111111111111111111111111",
            "to": "0x2222222222222222222222222222222222222222",
            "value": hex(52_956 * 10**18),
            "gas": hex(21000),
            "gasPrice": hex(20_000_000_000),
        },
        block_height=20123456,
        block_hash="0xblock",
        block_timestamp=1783000000,
        min_eth=5000,
    )

    assert transfer is not None
    assert transfer["chain"] == "eth"
    assert transfer["asset"] == "ETH"
    assert transfer["amount"] == 52956
    assert transfer["input_addresses"][0]["address"] == "0x1111111111111111111111111111111111111111"
    assert transfer["output_addresses"][0]["address"] == "0x2222222222222222222222222222222222222222"


def test_etherscan_provider_throttles_consecutive_requests(monkeypatch) -> None:
    now = [1000.0]
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"result": "0x1"}

    monkeypatch.setattr("backend.app.services.eth_large_transfer.time.monotonic", fake_monotonic)
    monkeypatch.setattr("backend.app.services.eth_large_transfer.time.sleep", fake_sleep)
    monkeypatch.setattr("backend.app.services.eth_large_transfer.requests.get", lambda *args, **kwargs: FakeResponse())

    provider = EthLargeTransferProvider("test-key", min_request_interval_seconds=0.25)
    provider._get_json({"module": "proxy", "action": "eth_blockNumber"})
    provider._get_json({"module": "proxy", "action": "eth_blockNumber"})

    assert sleeps == [0.25]


def test_eth_news_matches_large_transfer_by_amount_time_and_direction() -> None:
    transfer = parse_eth_large_transfer(
        {
            "hash": "0x" + "c" * 64,
            "from": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "value": hex(52_956 * 10**18),
            "gas": hex(21000),
            "gasPrice": hex(20_000_000_000),
        },
        block_height=20123456,
        block_hash="0xblock",
        block_timestamp=1783000000,
        min_eth=5000,
    )
    assert transfer is not None
    signal = {
        "id": "blackrock-eth-52956",
        "title": "BlackRock associated wallet deposited 52,956 ETH to Coinbase",
        "summary": "OnchainLens reported a BlackRock related address moved 2,700 BTC and 52,956 ETH to Coinbase.",
        "published_at": datetime.fromtimestamp(1783000000, tz=timezone.utc).isoformat(),
        "eth_amounts": [52956],
        "btc_amounts": [2700],
        "mentions_coinbase": True,
        "mentions_blackrock_or_ibit": True,
    }

    matches = match_news_signal_to_large_transfers(signal, [transfer], window_hours=48, amount_tolerance_pct=8, max_matches=20)

    assert matches
    assert matches[0]["chain"] == "eth"
    assert matches[0]["asset"] == "ETH"
    assert matches[0]["candidate_address"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert matches[0]["address_role"] == "source"


def test_news_without_txid_matches_large_transfer_by_behavior() -> None:
    transfer = parse_btc_large_transfer(_sample_tx(), block_height=900001, block_hash="block-hash", block_timestamp=1783000000, min_btc=500)
    assert transfer is not None
    signal = {
        "id": "blockbeats-blackrock-4917",
        "title": "贝莱德ETF地址向Coinbase存入4917枚BTC，价值约3.01亿美元",
        "summary": "据 OnchainLens 监测，贝莱德 ETF 地址向 Coinbase 存入 4,917 枚 BTC。",
        "published_at": datetime.fromtimestamp(1783000000, tz=timezone.utc).isoformat(),
        "btc_amounts": [4917],
        "usd_amounts": [301_000_000],
        "mentions_coinbase": True,
        "mentions_blackrock_or_ibit": True,
    }

    matches = match_news_signal_to_large_transfers(signal, [transfer], window_hours=48, amount_tolerance_pct=8, max_matches=20)

    assert matches
    assert matches[0]["candidate_address"].startswith("bc1source")
    assert matches[0]["address_role"] == "source"
    assert matches[0]["confidence"] >= 0.7
    assert any("BTC 数量" in reason for reason in matches[0]["reasons"])


def test_blackrock_case_matches_real_candidate_source_address() -> None:
    block_time = int(datetime(2026, 7, 2, 11, 22, 4, tzinfo=timezone.utc).timestamp())
    transfer = parse_btc_large_transfer(
        {
            "txid": "a00ec5e8dba31bc49c9b49ee2e551ea63b6736be7948933d128033199b1aa384",
            "fee": 17063,
            "status": {"confirmed": True, "block_height": 956347, "block_hash": "block-hash", "block_time": block_time},
            "vin": [
                {"prevout": {"scriptpubkey_address": "36YZXcTVLPdyapYuqXdJEt46oMVB2NrzVv", "value": 491_650_920_762}},
            ],
            "vout": [
                {"scriptpubkey_address": "3MqUP6G1daVS5YTD8fz3QgwjZortWwxXFd", "value": 405_736_052_547},
                {"scriptpubkey_address": "bc1qmakjy7ns2z8vwgptf9vs8fndp304fg0p9xafm2", "value": 85_914_851_152},
            ],
        },
        block_height=956347,
        block_hash="block-hash",
        block_timestamp=block_time,
        min_btc=500,
    )
    assert transfer is not None
    signal = {
        "id": "blockbeats-blackrock-20260702-4917",
        "title": "贝莱德ETF地址向Coinbase存入4917枚BTC，价值约3.01亿美元",
        "summary": "据 OnchainLens 监测，贝莱德 ETF 地址向 Coinbase 存入 4,917 枚 BTC。",
        "published_at": datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat(),
        "btc_amounts": [4917],
        "usd_amounts": [301_000_000],
        "mentions_coinbase": True,
        "mentions_blackrock_or_ibit": True,
    }

    matches = match_news_signal_to_large_transfers(signal, [transfer], window_hours=6, amount_tolerance_pct=8, max_matches=20)

    assert matches
    assert matches[0]["txid"] == "a00ec5e8dba31bc49c9b49ee2e551ea63b6736be7948933d128033199b1aa384"
    assert matches[0]["candidate_address"] == "36YZXcTVLPdyapYuqXdJEt46oMVB2NrzVv"
    assert matches[0]["address_role"] == "source"
    assert matches[0]["confidence"] >= 0.9


def test_btc_large_transfer_cleanup_removes_old_rows(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    transfer = parse_btc_large_transfer(_sample_tx(block_time=1000), block_height=900001, block_hash="block-hash", block_timestamp=1000, min_btc=500)
    assert transfer is not None
    store.upsert_btc_large_transfer(transfer)

    deleted = store.cleanup_btc_large_transfers(1, now=datetime.fromtimestamp(1783000000, tz=timezone.utc))

    assert deleted == 1
    assert store.list_btc_large_transfers(limit=10).total == 0
