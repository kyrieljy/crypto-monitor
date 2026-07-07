from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .btc_large_transfer import BtcLargeTransferError, BtcLargeTransferProvider, match_news_signal_to_large_transfers
from .eth_large_transfer import EthLargeTransferError, EthLargeTransferProvider
from .events import EventBus
from .store import Store
from .whale import BlackRockFreeProvider, DeBankProvider, HyperliquidProvider, WhaleProviderError, extract_addresses, extract_btc_addresses, merge_snapshots
from .whale import BlackRockFreeProvider as BlackRockFreeAddressBuilder


LOGGER = logging.getLogger("market_monitor.whale")


class WhaleRunner:
    def __init__(self, store: Store, bus: EventBus, timeout_seconds: int = 20) -> None:
        self.store = store
        self.bus = bus
        self.timeout_seconds = timeout_seconds

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:  # noqa: BLE001
                LOGGER.exception("巨鲸轮询失败")
            await asyncio.sleep(self._next_sleep())

    def _next_sleep(self) -> int:
        strategy = self.store.get_strategy("whale")
        if not strategy:
            return 300
        try:
            poll_seconds = int(strategy.config.get("poll_seconds", 180))
            if bool(strategy.config.get("trade_monitor_enabled", True)):
                poll_seconds = min(poll_seconds, int(strategy.config.get("trade_poll_seconds", 60)))
            return max(60, poll_seconds)
        except (TypeError, ValueError):
            return 180

    def run_once(self) -> None:
        strategy = self.store.get_strategy("whale")
        if strategy is None or not strategy.enabled or not bool(strategy.config.get("enabled", True)):
            return
        config = dict(strategy.config)
        secret = self.store.get_whale_secret()
        if bool(config.get("btc_candidate_monitor_enabled", True)):
            try:
                self.sync_btc_large_transfers_now(config=config)
            except Exception:  # noqa: BLE001
                LOGGER.exception("BTC 大额底表同步失败")
        if bool(config.get("eth_candidate_monitor_enabled", True)) and bool(config.get("etherscan_enabled", False)):
            try:
                self.sync_eth_large_transfers_now(config=config, secret=secret)
            except Exception:  # noqa: BLE001
                LOGGER.exception("ETH large transfer sync failed")
        targets = [target for target in self.store.list_whale_targets() if target.enabled]
        for target in targets:
            if _is_blackrock_free_target(target.config):
                try:
                    self._sync_blackrock_free_target(target, config)
                except Exception:  # noqa: BLE001
                    LOGGER.exception("IBIT 免费监控同步失败 target=%s", target.id)
                continue
            addresses = _target_addresses(target.address_or_subject, target.config)
            if not addresses:
                self.store.save_whale_snapshot(
                    target.id,
                    {
                        "positions": [],
                        "holdings": [],
                        "defi_positions": [],
                        "open_orders": [],
                        "account_summary": {},
                        "source_status": {"address": {"ok": False, "message": "未配置完整 EVM 地址"}},
                    },
                )
                continue
            try:
                self._sync_target(target, addresses, config, secret)
            except Exception:  # noqa: BLE001
                LOGGER.exception("巨鲸目标同步失败 target=%s", target.id)

    def sync_target_now(self, target_id: str, *, force_extended: bool = True) -> None:
        strategy = self.store.get_strategy("whale")
        if strategy is None or not strategy.enabled or not bool(strategy.config.get("enabled", True)):
            return
        target = self.store.get_whale_target(target_id)
        if target is None or not target.enabled:
            return
        if _is_blackrock_free_target(target.config):
            self._sync_blackrock_free_target(target, dict(strategy.config), force_lookback=force_extended)
            return
        addresses = _target_addresses(target.address_or_subject, target.config)
        if not addresses:
            self.store.save_whale_snapshot(
                target.id,
                {
                    "positions": [],
                    "holdings": [],
                    "defi_positions": [],
                    "open_orders": [],
                    "account_summary": {},
                    "source_status": {"address": {"ok": False, "message": "未配置完整 EVM 地址"}},
                },
            )
            return
        self._sync_target(target, addresses, dict(strategy.config), self.store.get_whale_secret(), force_extended=force_extended)

    def sync_btc_large_transfers_now(
        self,
        *,
        config: dict[str, Any] | None = None,
        blocks: int | None = None,
        start_utc: str | None = None,
        end_utc: str | None = None,
        max_blocks: int | None = None,
    ) -> dict[str, Any]:
        strategy = self.store.get_strategy("whale")
        merged_config = dict(strategy.config if strategy else {})
        if config:
            merged_config.update(config)
        provider = BtcLargeTransferProvider(
            str(merged_config.get("blackrock_blockstream_api") or "https://blockstream.info/api"),
            self.timeout_seconds,
        )
        min_btc = _float(merged_config.get("btc_candidate_min_btc"), 500)
        latest_height = provider.latest_height()
        if start_utc or end_utc:
            if not start_utc or not end_utc:
                raise BtcLargeTransferError("历史回扫需要同时提供 start_utc 和 end_utc")
            start_dt = _parse_iso_datetime(start_utc)
            end_dt = _parse_iso_datetime(end_utc)
            if end_dt < start_dt:
                raise BtcLargeTransferError("历史回扫结束时间不能早于开始时间")
            start_height = max(0, provider.height_at_or_before(int(start_dt.timestamp())) - 1)
            end_height = min(latest_height, provider.height_at_or_before(int(end_dt.timestamp())) + 2)
            range_limit = max(1, min(_int(max_blocks, 24), 288))
            if end_height - start_height + 1 > range_limit:
                start_height = max(start_height, end_height - range_limit + 1)
        elif blocks is not None:
            start_height = max(0, latest_height - max(1, int(blocks)) + 1)
            end_height = latest_height
        else:
            last_height = _int(self.store.state_get("btc_large_transfers:last_scanned_height"), 0)
            if last_height <= 0:
                backfill_blocks = max(1, _int(merged_config.get("btc_candidate_backfill_blocks"), 3))
                start_height = max(0, latest_height - backfill_blocks + 1)
            else:
                start_height = last_height + 1
            scan_blocks = max(1, _int(merged_config.get("btc_candidate_scan_blocks_per_run"), 1))
            end_height = min(latest_height, start_height + scan_blocks - 1)
        if start_height > latest_height:
            self.store.state_set("btc_large_transfers:last_scan_at", datetime.now(timezone.utc).isoformat())
            self.store.record_source_success(provider.source_name, provider.label)
            return {"ok": True, "scanned_blocks": 0, "inserted": 0, "latest_height": latest_height, "message": "no new blocks", "start_height": start_height, "end_height": end_height}
        inserted = 0
        scanned = 0
        try:
            for height in range(start_height, end_height + 1):
                transfers = provider.scan_block(height, min_btc=min_btc)
                for transfer in transfers:
                    inserted += int(self.store.upsert_btc_large_transfer(transfer))
                scanned += 1
                self.store.state_set("btc_large_transfers:last_scanned_height", str(height))
            self.store.state_set("btc_large_transfers:last_scan_at", datetime.now(timezone.utc).isoformat())
            self.store.record_source_success(provider.source_name, provider.label)
            if inserted and self.bus is not None:
                self.bus.publish_threadsafe("whale", {"btc_large_transfers": inserted})
            return {"ok": True, "scanned_blocks": scanned, "inserted": inserted, "latest_height": latest_height, "message": "", "start_height": start_height, "end_height": end_height}
        except BtcLargeTransferError as exc:
            self.store.record_source_error(provider.source_name, provider.label, str(exc))
            raise

    def sync_eth_large_transfers_now(
        self,
        *,
        config: dict[str, Any] | None = None,
        secret: dict[str, Any] | None = None,
        blocks: int | None = None,
        start_utc: str | None = None,
        end_utc: str | None = None,
        max_blocks: int | None = None,
    ) -> dict[str, Any]:
        strategy = self.store.get_strategy("whale")
        merged_config = dict(strategy.config if strategy else {})
        if config:
            merged_config.update(config)
        provider = self._eth_provider(merged_config, secret)
        min_eth = _float(merged_config.get("eth_candidate_min_eth"), 5000)
        latest_height = provider.latest_height()
        if start_utc or end_utc:
            if not start_utc or not end_utc:
                raise EthLargeTransferError("ETH history rescan requires start_utc and end_utc")
            start_dt = _parse_iso_datetime(start_utc)
            end_dt = _parse_iso_datetime(end_utc)
            if end_dt < start_dt:
                raise EthLargeTransferError("ETH history rescan end cannot be earlier than start")
            start_height = max(0, provider.height_at_or_before(int(start_dt.timestamp())) - 1)
            end_height = min(latest_height, provider.height_at_or_before(int(end_dt.timestamp())) + 2)
            range_limit = max(1, min(_int(max_blocks, 24), 288))
            if end_height - start_height + 1 > range_limit:
                start_height = max(start_height, end_height - range_limit + 1)
        elif blocks is not None:
            start_height = max(0, latest_height - max(1, int(blocks)) + 1)
            end_height = latest_height
        else:
            last_height = _int(self.store.state_get("eth_large_transfers:last_scanned_height"), 0)
            if last_height <= 0:
                backfill_blocks = max(1, _int(merged_config.get("eth_candidate_backfill_blocks"), 3))
                start_height = max(0, latest_height - backfill_blocks + 1)
            else:
                start_height = last_height + 1
            scan_blocks = max(1, _int(merged_config.get("eth_candidate_scan_blocks_per_run"), 1))
            end_height = min(latest_height, start_height + scan_blocks - 1)
        if start_height > latest_height:
            self.store.state_set("eth_large_transfers:last_scan_at", datetime.now(timezone.utc).isoformat())
            self.store.record_source_success(provider.source_name, provider.label)
            return {"ok": True, "scanned_blocks": 0, "inserted": 0, "latest_height": latest_height, "message": "no new blocks", "start_height": start_height, "end_height": end_height}
        return self._scan_eth_height_range(provider, start_height, end_height, min_eth=min_eth, update_cursor=not (start_utc or end_utc or blocks is not None), latest_height=latest_height)

    def sync_eth_large_transfers_for_news_now(
        self,
        signals: list[dict[str, Any]],
        *,
        config: dict[str, Any],
        secret: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if not bool(config.get("etherscan_enabled", False)):
            return {"ok": False, "skipped": True, "message": "Etherscan is disabled", "scanned_blocks": 0, "inserted": 0}
        provider = self._eth_provider(config, secret)
        min_eth = _float(config.get("eth_candidate_min_eth"), 5000)
        per_signal_limit = max(1, min(_int(config.get("eth_candidate_history_blocks_per_news"), 720), 2880))
        eth_signals = [signal for signal in signals if isinstance(signal, dict) and signal.get("eth_amounts")]
        if not eth_signals:
            if progress_callback:
                progress_callback({"stage": "扫描 ETH 底表", "message": "没有包含 ETH 金额的新闻线索", "progress": 100, "current": 0, "total": 0})
            return {"ok": True, "scanned_blocks": 0, "inserted": 0, "message": ""}
        max_scans = max(1, per_signal_limit * 3)
        estimated_total = max(1, min(max_scans, per_signal_limit * max(1, len(eth_signals))))
        progress_step = max(1, min(25, estimated_total // 20 or 1))
        seen_heights: set[int] = set()
        scanned = 0
        inserted = 0
        if progress_callback:
            progress_callback({"stage": "扫描 ETH 底表", "message": f"发现 {len(eth_signals)} 条包含 ETH 金额的新闻线索", "progress": 0, "current": 0, "total": estimated_total})
        for signal in eth_signals:
            if scanned >= max_scans:
                break
            for timestamp in _signal_candidate_timestamps(signal):
                if scanned >= max_scans:
                    break
                center = provider.height_at_or_before(int(timestamp))
                half = max(0, per_signal_limit // 2)
                start_height = max(0, center - half)
                end_height = center + max(0, per_signal_limit - half - 1)
                for height in range(start_height, end_height + 1):
                    if scanned >= max_scans:
                        break
                    if height in seen_heights:
                        continue
                    seen_heights.add(height)
                    transfers = provider.scan_block(height, min_eth=min_eth)
                    for transfer in transfers:
                        inserted += int(self.store.upsert_btc_large_transfer(transfer))
                    scanned += 1
                    if progress_callback and (scanned == 1 or scanned % progress_step == 0 or scanned >= estimated_total):
                        progress_callback(
                            {
                                "stage": "扫描 ETH 底表",
                                "message": f"已扫 {scanned}/{estimated_total} 个 ETH 区块，新增 {inserted} 笔底单",
                                "progress": min(100, scanned / estimated_total * 100),
                                "current": scanned,
                                "total": estimated_total,
                            }
                        )
        self.store.state_set("eth_large_transfers:last_scan_at", datetime.now(timezone.utc).isoformat())
        self.store.record_source_success(provider.source_name, provider.label)
        if inserted and self.bus is not None:
            self.bus.publish_threadsafe("whale", {"eth_large_transfers": inserted})
        if progress_callback:
            progress_callback({"stage": "扫描 ETH 底表", "message": f"ETH 区块扫描完成，新增 {inserted} 笔底单", "progress": 100, "current": scanned, "total": estimated_total})
        return {"ok": True, "scanned_blocks": scanned, "inserted": inserted, "message": ""}

    def _scan_eth_height_range(
        self,
        provider: EthLargeTransferProvider,
        start_height: int,
        end_height: int,
        *,
        min_eth: float,
        update_cursor: bool,
        latest_height: int | None = None,
    ) -> dict[str, Any]:
        inserted = 0
        scanned = 0
        try:
            for height in range(start_height, end_height + 1):
                transfers = provider.scan_block(height, min_eth=min_eth)
                for transfer in transfers:
                    inserted += int(self.store.upsert_btc_large_transfer(transfer))
                scanned += 1
                if update_cursor:
                    self.store.state_set("eth_large_transfers:last_scanned_height", str(height))
            self.store.state_set("eth_large_transfers:last_scan_at", datetime.now(timezone.utc).isoformat())
            self.store.record_source_success(provider.source_name, provider.label)
            if inserted and self.bus is not None:
                self.bus.publish_threadsafe("whale", {"eth_large_transfers": inserted})
            return {"ok": True, "scanned_blocks": scanned, "inserted": inserted, "latest_height": latest_height, "message": "", "start_height": start_height, "end_height": end_height}
        except EthLargeTransferError as exc:
            self.store.record_source_error(provider.source_name, provider.label, str(exc))
            raise

    def _eth_provider(self, config: dict[str, Any], secret: dict[str, Any] | None = None) -> EthLargeTransferProvider:
        api_key = str((secret or self.store.get_whale_secret()).get("etherscan_api_key") or "")
        if not api_key:
            raise EthLargeTransferError("Etherscan API key is not configured")
        return EthLargeTransferProvider(
            api_key,
            base_url=str(config.get("etherscan_api_url") or "https://api.etherscan.io/v2/api"),
            chain_id=str(config.get("etherscan_chain_id") or "1"),
            timeout_seconds=self.timeout_seconds,
            min_request_interval_seconds=_float(config.get("etherscan_min_request_interval_seconds"), 0.25),
        )

    def sync_ibit_history_now(
        self,
        target_id: str,
        *,
        lookback_days: int = 30,
        max_news_items: int = 300,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        def emit_progress(stage: str, message: str = "", progress: float | None = None, current: int | None = None, total: int | None = None) -> None:
            if progress_callback is None:
                return
            payload: dict[str, Any] = {"stage": stage, "message": message}
            if progress is not None:
                payload["progress"] = max(0.0, min(100.0, float(progress)))
            if current is not None:
                payload["current"] = current
            if total is not None:
                payload["total"] = total
            progress_callback(payload)

        target = self.store.get_whale_target(target_id)
        if target is None:
            raise KeyError(target_id)
        if not _is_blackrock_free_target(target.config):
            raise ValueError("target is not an IBIT monitor")
        strategy = self.store.get_strategy("whale")
        config = dict(strategy.config if strategy else {})
        lookback_days = max(1, min(int(lookback_days), 90))
        lookback_hours = lookback_days * 24
        config.update(
            {
                "blackrock_free_enabled": True,
                "blackrock_free_notification_enabled": False,
                "blackrock_etf_flow_notification_enabled": False,
                "blackrock_btc_outflow_notification_enabled": False,
                "blackrock_btc_address_operation_notification_enabled": False,
                "ibit_news_candidate_notification_enabled": False,
                "blackrock_btc_lookback_hours": lookback_hours,
                "ibit_news_enabled": True,
                "ibit_news_lookback_hours": lookback_hours,
                "ibit_news_max_items": max(10, int(max_news_items)),
            }
        )
        emit_progress("采集 IBIT/BTC/新闻", f"回看 {lookback_days} 天，正在拉取 IBIT 持仓、BTC 链地址和新闻线索", 8, 8, 100)
        self._sync_blackrock_free_target(target, config, force_lookback=True)
        emit_progress("读取回扫结果", "正在读取链上底单、地址簇和新闻匹配结果", 25, 25, 100)
        snapshot = self.store.get_whale_snapshot(target_id)
        raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
        cluster = raw.get("btc_cluster") if isinstance(raw.get("btc_cluster"), dict) else {}
        news = raw.get("news_signals") if isinstance(raw.get("news_signals"), dict) else {}
        signals = news.get("signals") if isinstance(news.get("signals"), list) else []
        suspected_addresses = news.get("suspected_addresses") if isinstance(news.get("suspected_addresses"), list) else []
        eth_inserted = 0
        eth_message = ""
        if signals and bool(config.get("etherscan_enabled", False)):
            try:
                def eth_progress(update: dict[str, Any]) -> None:
                    child_progress = _float(update.get("progress"))
                    emit_progress(
                        str(update.get("stage") or "扫描 ETH 底表"),
                        str(update.get("message") or ""),
                        35 + min(100.0, child_progress) * 0.45,
                        _int(update.get("current")),
                        _int(update.get("total")),
                    )

                emit_progress("扫描 ETH 底表", "正在按新闻时间定位 ETH 区块并扫描大额转账", 35, 0, 0)
                eth_result = self.sync_eth_large_transfers_for_news_now(signals, config=config, secret=self.store.get_whale_secret(), progress_callback=eth_progress)
                eth_inserted = _int(eth_result.get("inserted"))
                emit_progress("匹配新闻线索", "正在把 BTC/ETH 底表和新闻金额、时间窗口做匹配", 82, 82, 100)
                self._attach_btc_large_transfer_matches(target_id, snapshot, config)
                raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
                news = raw.get("news_signals") if isinstance(raw.get("news_signals"), dict) else {}
                signals = news.get("signals") if isinstance(news.get("signals"), list) else []
                suspected_addresses = news.get("suspected_addresses") if isinstance(news.get("suspected_addresses"), list) else []
            except Exception as exc:  # noqa: BLE001
                eth_message = f"ETH history scan skipped: {str(exc)[:200]}"
                emit_progress("扫描 ETH 底表", eth_message, 82, 82, 100)
        elif signals:
            emit_progress("跳过 ETH 底表", "Etherscan 数据源未启用，仅使用已有 BTC/ETH 底表做新闻匹配", 82, 82, 100)
        else:
            emit_progress("跳过 ETH 底表", "没有找到可匹配的新闻线索", 82, 82, 100)
        emit_progress("保存结果", "正在写入回扫工作流结果", 92, 92, 100)
        result = {
            "ok": True,
            "target_id": target_id,
            "lookback_days": lookback_days,
            "address_count": len(cluster.get("addresses") if isinstance(cluster.get("addresses"), list) else _target_btc_addresses(target.address_or_subject, target.config)),
            "account_operation_count": _activity_count(cluster.get("address_activity")),
            "news_signal_count": len(signals),
            "matched_address_count": _matched_address_count(signals, suspected_addresses),
            "large_transfer_match_count": _int(news.get("btc_large_transfer_match_count")),
            "eth_large_transfer_inserted": eth_inserted,
            "message": eth_message,
        }
        raw["history_workflow"] = {**result, "synced_at": datetime.now(timezone.utc).isoformat()}
        snapshot["raw"] = raw
        self.store.save_whale_snapshot(target_id, snapshot)
        emit_progress("完成", "IBIT 历史回扫完成", 100, 100, 100)
        return result

    def _sync_blackrock_free_target(self, target: Any, config: dict[str, Any], *, force_lookback: bool = False) -> None:
        target_id = str(target.id)
        target_config = target.config if isinstance(target.config, dict) else {}
        if not bool(config.get("blackrock_free_enabled", True)):
            self.store.save_whale_snapshot(
                target_id,
                {
                    "positions": [],
                    "holdings": [],
                    "defi_positions": [],
                    "open_orders": [],
                    "account_summary": {"source": "blackrock_free"},
                    "source_status": {"blackrock_free": {"ok": False, "skipped": True, "message": "未启用 IBIT 免费监控"}},
                },
            )
            return

        old_snapshot = self.store.get_whale_snapshot(target_id)
        btc_addresses = _target_btc_addresses(target.address_or_subject, target_config)
        provider = BlackRockFreeProvider(
            ishares_url=str(config.get("blackrock_ishares_url") or target_config.get("ishares_url") or "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf"),
            farside_url=str(config.get("blackrock_farside_url") or target_config.get("farside_url") or "https://farside.co.uk/btc/"),
            blockstream_base_url=str(config.get("blackrock_blockstream_api") or target_config.get("blockstream_api") or "https://blockstream.info/api"),
            timeout_seconds=self.timeout_seconds,
        )
        lookback_hours = max(1, _int(config.get("blackrock_btc_lookback_hours"), 24))
        snapshot = provider.fetch(
            btc_addresses=btc_addresses,
            suspected_btc_addresses=_target_suspected_btc_addresses(target_config),
            transfer_min_btc=_float(config.get("blackrock_btc_transfer_min_btc"), 1000),
            transfer_lookback_hours=lookback_hours if not force_lookback else max(lookback_hours, 24),
            news_enabled=bool(config.get("ibit_news_enabled", False)),
            news_feed_urls=_string_list(config.get("ibit_news_rss_urls") or target_config.get("news_feed_urls")),
            news_keywords=_string_list(config.get("ibit_news_keywords")),
            news_lookback_hours=max(1, _int(config.get("ibit_news_lookback_hours"), 72)),
            news_max_items=max(1, _int(config.get("ibit_news_max_items"), 60)),
        ).to_dict()
        _preserve_btc_cluster_on_failure(snapshot, old_snapshot)
        self._store_ibit_address_operations_as_large_transfers(snapshot)
        self._attach_btc_large_transfer_matches(target_id, snapshot, config)

        source_status = snapshot.get("source_status") if isinstance(snapshot.get("source_status"), dict) else {}
        if any(isinstance(status, dict) and status.get("ok") for status in source_status.values()):
            self.store.record_source_success(provider.source_name, provider.label)
        else:
            self.store.record_source_error(provider.source_name, provider.label, "IBIT 免费监控所有数据源均不可用")

        self._record_blackrock_free_events(target, old_snapshot, snapshot, config, initial=not bool(old_snapshot))
        self.store.save_whale_snapshot(target_id, snapshot)
        if self.bus is not None:
            self.bus.publish_threadsafe("whale", {"target_id": target_id})

    def _store_ibit_address_operations_as_large_transfers(self, snapshot: dict[str, Any]) -> int:
        raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
        cluster = raw.get("btc_cluster") if isinstance(raw.get("btc_cluster"), dict) else {}
        activity = cluster.get("address_activity") if isinstance(cluster.get("address_activity"), dict) else {}
        inserted = 0
        for address, operations in activity.items():
            if not isinstance(operations, list):
                continue
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                transfer = _btc_transfer_from_address_operation(str(address), operation)
                if transfer:
                    inserted += int(self.store.upsert_btc_large_transfer(transfer))
        return inserted

    def _attach_btc_large_transfer_matches(self, target_id: str, snapshot: dict[str, Any], config: dict[str, Any]) -> None:
        raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
        news = raw.get("news_signals") if isinstance(raw.get("news_signals"), dict) else {}
        signals = news.get("signals") if isinstance(news.get("signals"), list) else []
        if not signals:
            return
        window_hours = max(1, _int(config.get("btc_candidate_match_window_hours"), 48))
        tolerance_pct = max(1.0, _float(config.get("btc_candidate_amount_tolerance_pct"), 8))
        max_matches = max(1, _int(config.get("btc_candidate_max_matches_per_news"), 20))
        matched_count = 0
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            published_at = _parse_iso_datetime(str(signal.get("published_at") or ""))
            candidates = self.store.list_btc_large_transfer_candidates(
                start_utc=(published_at - _hours(window_hours)).isoformat(),
                end_utc=(published_at + _hours(window_hours)).isoformat(),
                min_btc=0,
                limit=1000,
            )
            matches = match_news_signal_to_large_transfers(
                signal,
                candidates,
                window_hours=window_hours,
                amount_tolerance_pct=tolerance_pct,
                max_matches=max_matches,
            )
            if matches:
                signal["large_transfer_matches"] = matches
                existing_addresses = [str(item) for item in signal.get("candidate_addresses", []) if item]
                matched_addresses = [str(match.get("candidate_address") or "") for match in matches if match.get("candidate_address")]
                signal["candidate_addresses"] = list(dict.fromkeys([*existing_addresses, *matched_addresses]))
                existing_txids = [str(item) for item in signal.get("txids", []) if item]
                matched_txids = [str(match.get("txid") or "") for match in matches if match.get("txid")]
                signal["txids"] = list(dict.fromkeys([*existing_txids, *matched_txids]))
                matched_count += len(matches)
        suspected_activity = news.get("suspected_address_activity") if isinstance(news.get("suspected_address_activity"), dict) else {}
        confirmed_activity = news.get("confirmed_address_activity") if isinstance(news.get("confirmed_address_activity"), dict) else {}
        news["suspected_addresses"] = BlackRockFreeAddressBuilder.build_suspected_addresses(signals, candidate_activity={**confirmed_activity, **suspected_activity})
        news["btc_large_transfer_match_count"] = matched_count
        account = snapshot.get("account_summary") if isinstance(snapshot.get("account_summary"), dict) else {}
        account["ibit_btc_large_transfer_match_count"] = matched_count
        account["ibit_suspected_address_count"] = len(news["suspected_addresses"])
        self.store.save_btc_news_matches(target_id, signals)

    def _sync_target(self, target: Any, addresses: list[str], config: dict[str, Any], secret: dict[str, Any], *, force_extended: bool = False) -> None:
        target_id = str(target.id)
        old_snapshot = self.store.get_whale_snapshot(target_id)
        snapshots = []
        source_status: dict[str, Any] = {}
        for address in addresses:
            if bool(config.get("hyperliquid_enabled", True)):
                provider = HyperliquidProvider(str(config.get("hyperliquid_base_url") or "https://api.hyperliquid.xyz"), self.timeout_seconds)
                try:
                    include_extended = force_extended or self._should_refresh_extended(target.id, address, config)
                    snapshot = provider.fetch(address, include_extended=include_extended)
                    if not include_extended:
                        _preserve_extended_snapshot_fields(snapshot, old_snapshot)
                    snapshot.account_summary["address"] = address
                    snapshots.append(snapshot)
                    if bool(config.get("trade_monitor_enabled", True)):
                        self._sync_fills(target, address, provider, snapshot.fills, config, current_positions=snapshot.positions, old_positions=old_snapshot.get("positions", []))
                    if include_extended:
                        self._mark_extended_refreshed(target.id, address)
                    self.store.record_source_success(provider.source_name, provider.label)
                except WhaleProviderError as exc:
                    source_status[provider.source_name] = {"ok": False, "message": str(exc)}
                    self.store.record_source_error(provider.source_name, provider.label, str(exc))
            if bool(config.get("debank_enabled", False)):
                provider = DeBankProvider(
                    str(config.get("debank_base_url") or "https://pro-openapi.debank.com"),
                    str(secret.get("debank_access_key") or secret.get("api_key") or ""),
                    self.timeout_seconds,
                )
                try:
                    snapshot = provider.fetch(address)
                    snapshot.account_summary["address"] = address
                    snapshots.append(snapshot)
                    if snapshot.source_status.get("debank", {}).get("ok"):
                        self.store.record_source_success(provider.source_name, provider.label)
                except WhaleProviderError as exc:
                    source_status[provider.source_name] = {"ok": False, "message": str(exc)}
                    self.store.record_source_error(provider.source_name, provider.label, str(exc))
        merged = merge_snapshots(snapshots)
        merged["source_status"] = {**merged.get("source_status", {}), **source_status}
        self._record_events(target_id, old_snapshot, merged, config)
        self.store.save_whale_snapshot(target_id, merged)
        if self.bus is not None:
            self.bus.publish_threadsafe("whale", {"target_id": target_id})

    def _should_refresh_extended(self, target_id: str, address: str, config: dict[str, Any]) -> bool:
        interval = max(300, _int(config.get("extended_poll_seconds"), 900))
        last = _int(self.store.state_get(_extended_cursor_key(target_id, address)))
        return last <= 0 or _now_ms() - last >= interval * 1000

    def _mark_extended_refreshed(self, target_id: str, address: str) -> None:
        self.store.state_set(_extended_cursor_key(target_id, address), str(_now_ms()))

    def _sync_fills(
        self,
        target: Any,
        address: str,
        provider: HyperliquidProvider,
        recent_fills: list[dict[str, Any]],
        config: dict[str, Any],
        *,
        current_positions: list[dict[str, Any]] | None = None,
        old_positions: list[dict[str, Any]] | None = None,
    ) -> None:
        cursor_key = _fill_cursor_key(str(target.id), address)
        now_ms = _now_ms()
        cursor = _load_cursor(self.store.state_get(cursor_key))
        if cursor is None:
            start_ms = _initial_fill_start_ms(config, now_ms)
            if start_ms is None:
                latest_time = max([_int(fill.get("timestamp")) for fill in recent_fills], default=now_ms)
                self.store.state_set(cursor_key, json.dumps({"last_time_ms": latest_time}, sort_keys=True))
                return
            fills = self._fetch_incremental_fills(provider, address, start_ms, now_ms)
            self._record_fill_events(
                target,
                address,
                fills,
                config,
                notify_allowed=str(config.get("initial_fill_sync_mode") or "cursor_only") != "cursor_only",
                current_positions=current_positions,
                old_positions=old_positions,
            )
            latest_time = max([_int(fill.get("timestamp")) for fill in fills], default=now_ms)
            self.store.state_set(cursor_key, json.dumps({"last_time_ms": latest_time}, sort_keys=True))
            return

        start_ms = max(0, int(cursor.get("last_time_ms", 0)) + 1)
        if start_ms > now_ms:
            return
        fills = self._fetch_incremental_fills(provider, address, start_ms, now_ms)
        self._record_fill_events(
            target,
            address,
            fills,
            config,
            notify_allowed=True,
            current_positions=current_positions,
            old_positions=old_positions,
        )
        latest_time = max([_int(fill.get("timestamp")) for fill in fills], default=int(cursor.get("last_time_ms", 0)))
        self.store.state_set(cursor_key, json.dumps({"last_time_ms": max(latest_time, int(cursor.get("last_time_ms", 0)))}, sort_keys=True))

    def _fetch_incremental_fills(self, provider: HyperliquidProvider, address: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        fills: list[dict[str, Any]] = []
        cursor = start_ms
        for _ in range(5):
            page = provider.fetch_fills_by_time(address, cursor, end_ms)
            if not page:
                break
            page_sorted = sorted(page, key=lambda item: _int(item.get("timestamp")))
            fills.extend(page_sorted)
            max_time = max(_int(item.get("timestamp")) for item in page_sorted)
            if max_time <= cursor or len(page_sorted) < 2000:
                break
            cursor = max_time + 1
        deduped: dict[str, dict[str, Any]] = {}
        for fill in fills:
            deduped[_fill_identity(fill)] = fill
        return sorted(deduped.values(), key=lambda item: _int(item.get("timestamp")))

    def _record_fill_events(
        self,
        target: Any,
        address: str,
        fills: list[dict[str, Any]],
        config: dict[str, Any],
        *,
        notify_allowed: bool,
        current_positions: list[dict[str, Any]] | None = None,
        old_positions: list[dict[str, Any]] | None = None,
    ) -> None:
        if not bool(config.get("trade_notification_enabled", True)):
            notify_allowed = False
        for fill in sorted(fills, key=lambda item: _int(item.get("timestamp"))):
            is_large = _is_large_fill(fill, config)
            fill_context = _fill_event_context(fill, current_positions or [], old_positions or [])
            coin = str(fill.get("coin") or "")
            side = str(fill.get("side") or "")
            size = _float(fill.get("size"))
            price = _float(fill.get("price"))
            notional = _float(fill.get("notional"))
            summary = f"{target.label} {side}{coin} {size:g} @ ${price:,.2f}，成交额 ${notional:,.0f}"
            self.store.add_whale_event(
                provider="hyperliquid",
                target_id=str(target.id),
                action_type=("large_trade_buy" if str(fill.get("side_code")) == "B" else "large_trade_sell") if is_large else ("trade_buy" if str(fill.get("side_code")) == "B" else "trade_sell"),
                summary=summary,
                payload={
                    "target_label": target.label,
                    "address": address,
                    "fill": fill,
                    "is_large": is_large,
                    "hyperdash_url": f"https://hyperdash.info/trader/{address}",
                    **fill_context,
                },
                occurred_at_utc=_fill_time_iso(fill),
                event_key=f"hyperliquid:{target.id}:{address.lower()}:{_fill_identity(fill)}",
                notification_required=notify_allowed,
            )

    def _record_events(self, target_id: str, old_snapshot: dict[str, Any], new_snapshot: dict[str, Any], config: dict[str, Any]) -> None:
        threshold_pct = _float(config.get("position_change_alert_pct"), 25.0)
        min_value = _float(config.get("min_position_value_usd"), 10000.0)
        liquidation_distance = _float(config.get("liquidation_distance_pct"), 5.0)
        old_positions = {_position_key(item): item for item in old_snapshot.get("positions", []) if isinstance(item, dict)}
        new_positions = {_position_key(item): item for item in new_snapshot.get("positions", []) if isinstance(item, dict)}
        for key, position in new_positions.items():
            notional = abs(_float(position.get("notional")))
            if notional < min_value:
                continue
            old = old_positions.get(key)
            if old is None:
                self.store.add_whale_event(
                    provider="hyperliquid",
                    target_id=target_id,
                    action_type="open_position",
                    summary=f"{position.get('symbol')} 新增{position.get('side')}仓位 ${notional:,.0f}",
                    payload={"position": position, "current_position_label": _position_label(position)},
                )
                continue
            old_notional = abs(_float(old.get("notional")))
            if old_notional > 0:
                change_pct = abs(notional - old_notional) / old_notional * 100
                if change_pct >= threshold_pct:
                    self.store.add_whale_event(
                        provider="hyperliquid",
                        target_id=target_id,
                        action_type="position_change",
                        summary=f"{position.get('symbol')} 仓位变化 {change_pct:.1f}%",
                        payload={"old": old, "position": position, "change_pct": change_pct, "current_position_label": _position_label(position)},
                    )
            distance = position.get("liquidation_distance_pct")
            old_distance = old.get("liquidation_distance_pct") if isinstance(old, dict) else None
            if distance is not None and float(distance) <= liquidation_distance and (old_distance is None or float(old_distance) > liquidation_distance):
                self.store.add_whale_event(
                    provider="hyperliquid",
                    target_id=target_id,
                    action_type="liquidation_risk",
                    summary=f"{position.get('symbol')} 强平距离 {float(distance):.2f}%",
                    payload={"position": position, "current_position_label": _position_label(position)},
                )
        for key, position in old_positions.items():
            if key not in new_positions and abs(_float(position.get("notional"))) >= min_value:
                self.store.add_whale_event(
                    provider="hyperliquid",
                    target_id=target_id,
                    action_type="close_position",
                    summary=f"{position.get('symbol')} 已平{position.get('side')}仓位",
                    payload={"position": position, "current_position_label": _position_size_label(0, position.get("coin") or position.get("symbol"))},
                )

    def _record_blackrock_free_events(self, target: Any, old_snapshot: dict[str, Any], new_snapshot: dict[str, Any], config: dict[str, Any], *, initial: bool) -> None:
        etf_flow_notify_allowed = _blackrock_notification_enabled(config, "blackrock_etf_flow_notification_enabled")
        btc_outflow_notify_allowed = _blackrock_notification_enabled(config, "blackrock_btc_outflow_notification_enabled")
        btc_operation_notify_allowed = _blackrock_notification_enabled(config, "blackrock_btc_address_operation_notification_enabled")
        news_notify_allowed = _blackrock_notification_enabled(config, "ibit_news_candidate_notification_enabled")
        account = new_snapshot.get("account_summary") if isinstance(new_snapshot.get("account_summary"), dict) else {}
        old_account = old_snapshot.get("account_summary") if isinstance(old_snapshot.get("account_summary"), dict) else {}
        flow_threshold = _float(config.get("blackrock_flow_alert_min_usd"), 50_000_000)
        current_flow_date = str(account.get("blackrock_last_flow_date") or "")
        old_flow_date = str(old_account.get("blackrock_last_flow_date") or "")
        flow_usd = _float(account.get("blackrock_last_flow_usd"))
        if not initial and current_flow_date and current_flow_date != old_flow_date and abs(flow_usd) >= flow_threshold:
            summary = f"{target.label} IBIT ETF资金流 {flow_usd / 1_000_000:,.1f} 百万美元 ({current_flow_date})"
            self.store.add_whale_event(
                provider="blackrock_free",
                target_id=str(target.id),
                action_type="blackrock_etf_flow",
                summary=summary,
                payload={"target_label": target.label, "source": "Farside", "flow_usd": flow_usd, "date": current_flow_date},
                event_key=f"blackrock-free:farside:{target.id}:{current_flow_date}",
                notification_required=etf_flow_notify_allowed,
            )

        current_official_date = str(account.get("blackrock_official_date") or "")
        old_official_date = str(old_account.get("blackrock_official_date") or "")
        if not initial and current_official_date and current_official_date != old_official_date:
            summary = f"{target.label} 官方 IBIT 数据更新：净资产 ${_float(account.get('blackrock_official_net_assets')):,.0f}"
            self.store.add_whale_event(
                provider="blackrock_free",
                target_id=str(target.id),
                action_type="blackrock_official_update",
                summary=summary,
                payload={"target_label": target.label, "source": "iShares", "official": account},
                event_key=f"blackrock-free:ishares:{target.id}:{current_official_date}",
                notification_required=False,
            )

        raw = new_snapshot.get("raw") if isinstance(new_snapshot.get("raw"), dict) else {}
        cluster = raw.get("btc_cluster") if isinstance(raw.get("btc_cluster"), dict) else {}
        transfers = cluster.get("transfers") if isinstance(cluster.get("transfers"), list) else []
        initial_notify = bool(config.get("blackrock_initial_notification_enabled", False))
        for transfer in transfers:
            if not isinstance(transfer, dict):
                continue
            amount = _float(transfer.get("amount_btc"))
            summary = f"{target.label} 已确认 BTC 地址簇转出 {amount:g} BTC"
            self.store.add_whale_event(
                provider="blackrock_free",
                target_id=str(target.id),
                action_type="blackrock_confirmed_btc_outflow",
                summary=summary,
                payload={"target_label": target.label, "source": "Blockstream", "transfer": transfer},
                occurred_at_utc=_ms_to_iso(_int(transfer.get("timestamp_ms"))),
                event_key=f"blackrock-free:btc:{target.id}:{transfer.get('txid')}",
                notification_required=btc_outflow_notify_allowed and (not initial or initial_notify),
            )

        old_operation_keys = _blackrock_operation_keys(old_snapshot)
        address_activity = cluster.get("address_activity") if isinstance(cluster.get("address_activity"), dict) else {}
        for address, operations in address_activity.items():
            if not isinstance(operations, list):
                continue
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                operation_key = _btc_operation_identity(str(address), operation)
                if not operation_key:
                    continue
                amount = _float(operation.get("amount_btc"))
                behavior = str(operation.get("behavior") or operation.get("direction") or "BTC链上操作")
                short_address = f"{str(address)[:10]}...{str(address)[-6:]}" if len(str(address)) > 18 else str(address)
                self.store.add_whale_event(
                    provider="blackrock_free",
                    target_id=str(target.id),
                    action_type="blackrock_btc_address_operation",
                    summary=f"{target.label} BTC地址 {short_address} {behavior} {amount:g} BTC",
                    payload={"target_label": target.label, "source": "Blockstream", "operation": {**operation, "address": str(address)}},
                    occurred_at_utc=_ms_to_iso(_int(operation.get("timestamp_ms"))),
                    event_key=f"blackrock-free:btc-op:{target.id}:{operation_key}",
                    notification_required=btc_operation_notify_allowed and (not initial or operation_key not in old_operation_keys),
                )

        news = raw.get("news_signals") if isinstance(raw.get("news_signals"), dict) else {}
        signals = news.get("signals") if isinstance(news.get("signals"), list) else []
        old_raw = old_snapshot.get("raw") if isinstance(old_snapshot.get("raw"), dict) else {}
        old_news = old_raw.get("news_signals") if isinstance(old_raw.get("news_signals"), dict) else {}
        old_signals = old_news.get("signals") if isinstance(old_news.get("signals"), list) else []
        old_signal_ids = {str(item.get("id")) for item in old_signals if isinstance(item, dict) and item.get("id")}
        news_baseline_only = not bool(old_signal_ids)
        min_confidence = _float(config.get("ibit_news_candidate_notify_min_confidence"), 0.6)
        initial_news_notify = bool(config.get("ibit_news_initial_notification_enabled", False))
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            signal_id = str(signal.get("id") or "")
            if news_baseline_only or signal_id in old_signal_ids:
                continue
            confidence = _float(signal.get("confidence"))
            addresses = signal.get("candidate_addresses") if isinstance(signal.get("candidate_addresses"), list) else []
            txids = signal.get("txids") if isinstance(signal.get("txids"), list) else []
            if not addresses and not txids:
                continue
            title = str(signal.get("title") or "IBIT 新闻线索")
            summary = f"{target.label} 新闻线索：{title[:80]}（疑似地址 {len(addresses)} 个，txid {len(txids)} 个，置信度 {confidence * 100:.0f}%）"
            self.store.add_whale_event(
                provider="blackrock_free",
                target_id=str(target.id),
                action_type="ibit_news_address_candidate",
                summary=summary,
                payload={"target_label": target.label, "source": "IBIT News", "signal": signal},
                occurred_at_utc=str(signal.get("published_at") or ""),
                event_key=f"blackrock-free:news:{target.id}:{signal_id}",
                notification_required=news_notify_allowed and confidence >= min_confidence and (not initial or initial_news_notify),
            )


def _is_blackrock_free_target(config: dict[str, Any]) -> bool:
    return str(config.get("provider") or "").strip().lower() == "blackrock_free_monitor"


def _blackrock_notification_enabled(config: dict[str, Any], key: str) -> bool:
    if key in config:
        return bool(config.get(key))
    return bool(config.get("blackrock_free_notification_enabled", True))


def _blackrock_operation_keys(snapshot: dict[str, Any]) -> set[str]:
    raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
    cluster = raw.get("btc_cluster") if isinstance(raw.get("btc_cluster"), dict) else {}
    activity = cluster.get("address_activity") if isinstance(cluster.get("address_activity"), dict) else {}
    keys: set[str] = set()
    for address, operations in activity.items():
        if not isinstance(operations, list):
            continue
        for operation in operations:
            if isinstance(operation, dict):
                key = _btc_operation_identity(str(address), operation)
                if key:
                    keys.add(key)
    return keys


def _btc_operation_identity(address: str, operation: dict[str, Any]) -> str:
    txid = str(operation.get("txid") or "")
    if not txid:
        return ""
    direction = str(operation.get("direction") or operation.get("behavior") or "")
    return f"{address.lower()}:{txid}:{direction}"


def _preserve_btc_cluster_on_failure(snapshot: dict[str, Any], old_snapshot: dict[str, Any]) -> None:
    source_status = snapshot.get("source_status") if isinstance(snapshot.get("source_status"), dict) else {}
    btc_status = source_status.get("btc_cluster") if isinstance(source_status.get("btc_cluster"), dict) else {}
    if btc_status.get("ok") is not False:
        return
    raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
    old_raw = old_snapshot.get("raw") if isinstance(old_snapshot.get("raw"), dict) else {}
    old_cluster = old_raw.get("btc_cluster") if isinstance(old_raw.get("btc_cluster"), dict) else None
    if not old_cluster:
        return
    raw["btc_cluster"] = old_cluster
    news = raw.get("news_signals") if isinstance(raw.get("news_signals"), dict) else {}
    old_news = old_raw.get("news_signals") if isinstance(old_raw.get("news_signals"), dict) else {}
    if isinstance(news, dict) and "confirmed_address_activity" not in news and isinstance(old_news.get("confirmed_address_activity"), dict):
        news["confirmed_address_activity"] = old_news["confirmed_address_activity"]
        raw["news_signals"] = news
    snapshot["raw"] = raw
    account = snapshot.get("account_summary") if isinstance(snapshot.get("account_summary"), dict) else {}
    addresses = old_cluster.get("addresses") if isinstance(old_cluster.get("addresses"), list) else []
    transfers = old_cluster.get("transfers") if isinstance(old_cluster.get("transfers"), list) else []
    activity = old_cluster.get("address_activity") if isinstance(old_cluster.get("address_activity"), dict) else {}
    account["blackrock_btc_cluster_address_count"] = len(addresses)
    account["blackrock_btc_cluster_transfer_count"] = len(transfers)
    account["blackrock_btc_cluster_operation_count"] = _activity_count(activity)
    latest = transfers[0] if transfers and isinstance(transfers[0], dict) else None
    if latest:
        account["blackrock_btc_cluster_last_outflow_btc"] = latest.get("amount_btc")
    snapshot["account_summary"] = account


def _btc_transfer_from_address_operation(address: str, operation: dict[str, Any]) -> dict[str, Any] | None:
    txid = str(operation.get("txid") or "")
    amount_btc = _float(operation.get("amount_btc"))
    if not txid or amount_btc <= 0:
        return None
    direction = str(operation.get("direction") or "")
    input_btc = _float(operation.get("input_btc"))
    output_btc = _float(operation.get("output_btc"))
    input_addresses: list[dict[str, Any]] = []
    output_addresses: list[dict[str, Any]] = []
    if direction == "out":
        input_addresses.append({"address": address, "value": input_btc or amount_btc, "value_btc": input_btc or amount_btc, "role": "watched"})
        output_addresses.extend(_counterparty_items(operation.get("output_counterparties"), role="counterparty"))
    elif direction == "in":
        input_addresses.extend(_counterparty_items(operation.get("input_counterparties"), role="counterparty"))
        output_addresses.append({"address": address, "value": output_btc or amount_btc, "value_btc": output_btc or amount_btc, "role": "watched"})
    else:
        input_addresses.append({"address": address, "value": input_btc or amount_btc, "value_btc": input_btc or amount_btc, "role": "watched"})
        output_addresses.append({"address": address, "value": output_btc or amount_btc, "value_btc": output_btc or amount_btc, "role": "watched"})
    timestamp = str(operation.get("timestamp") or "")
    if not timestamp:
        timestamp_ms = _int(operation.get("timestamp_ms"))
        if timestamp_ms > 0:
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()
    return {
        "txid": txid,
        "chain": "btc",
        "asset": "BTC",
        "block_height": _int(operation.get("block_height")),
        "block_hash": str(operation.get("block_hash") or ""),
        "block_time_utc": timestamp,
        "amount": amount_btc,
        "amount_btc": amount_btc,
        "total_input_amount": input_btc or amount_btc,
        "total_output_amount": output_btc or amount_btc,
        "fee_amount": 0.0,
        "total_input_btc": input_btc or amount_btc,
        "total_output_btc": output_btc or amount_btc,
        "fee_btc": 0.0,
        "input_addresses": input_addresses[:80],
        "output_addresses": output_addresses[:80],
        "address_operations": [
            {
                "address": address,
                "direction": direction,
                "asset": "BTC",
                "amount": amount_btc,
                "value_btc": amount_btc,
                "amount_btc": amount_btc,
                "net_amount": _float(operation.get("net_btc")),
                "net_btc": _float(operation.get("net_btc")),
                "confirmed": bool(operation.get("confirmed")),
            }
        ],
        "exchange_hints": [],
        "source_url": str(operation.get("source_url") or f"https://blockstream.info/tx/{txid}"),
        "raw": {"source": "ibit_address_activity", "watched_address": address, "operation": operation},
    }


def _counterparty_items(value: Any, *, role: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            address = str(item.get("address") or "")
            amount = _float(item.get("value_btc"))
        else:
            address = str(item or "")
            amount = 0.0
        if address:
            rows.append({"address": address, "value": amount, "value_btc": amount, "role": role})
    return rows


def _activity_count(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    return sum(len(rows) for rows in value.values() if isinstance(rows, list))


def _matched_address_count(signals: list[Any], address_rows: list[Any]) -> int:
    signal_ids = {str(signal.get("id") or "") for signal in signals if isinstance(signal, dict) and signal.get("id")}
    matched: set[str] = set()
    for row in address_rows:
        if not isinstance(row, dict):
            continue
        address = str(row.get("address") or "")
        rows = row.get("signals") if isinstance(row.get("signals"), list) else []
        if address and any(isinstance(signal, dict) and str(signal.get("id") or "") in signal_ids for signal in rows):
            matched.add(address)
    return len(matched)


def _target_btc_addresses(address_or_subject: str, config: dict[str, Any]) -> list[str]:
    addresses = extract_btc_addresses(address_or_subject)
    for item in config.get("btc_addresses", []):
        addresses.extend(extract_btc_addresses(str(item)))
    return list(dict.fromkeys(addresses))


def _target_suspected_btc_addresses(config: dict[str, Any]) -> list[str]:
    addresses: list[str] = []
    for key in ("suspected_btc_addresses", "candidate_btc_addresses"):
        for item in config.get(key, []):
            addresses.extend(extract_btc_addresses(str(item)))
    return list(dict.fromkeys(addresses))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n,]+", value) if item.strip()]
    return []


def _target_addresses(address_or_subject: str, config: dict[str, Any]) -> list[str]:
    addresses = extract_addresses(address_or_subject)
    for item in config.get("addresses", []):
        addresses.extend(extract_addresses(str(item)))
    return list(dict.fromkeys(addresses))


def _position_key(position: dict[str, Any]) -> str:
    return f"{position.get('symbol')}:{position.get('side')}"


def _fill_event_context(fill: dict[str, Any], current_positions: list[dict[str, Any]], old_positions: list[dict[str, Any]]) -> dict[str, Any]:
    position = _find_fill_position(fill, current_positions) or _find_fill_position(fill, old_positions)
    context: dict[str, Any] = {
        "direction_label": str(fill.get("direction_label") or _direction_label(fill)),
        "price_label": str(fill.get("price_label") or _price_label(fill)),
    }
    current_position_size = _current_position_after_fill(fill)
    if current_position_size is not None:
        context["current_position_size"] = current_position_size
        context["current_position_label"] = _position_size_label(current_position_size, fill.get("coin") or fill.get("symbol"))
    elif position:
        position_label = _position_label(position)
        if position_label:
            context["current_position_label"] = position_label
    if position:
        context["position_leverage"] = position.get("leverage")
        context["position_margin_mode"] = position.get("margin_mode")
    return context


def _find_fill_position(fill: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any] | None:
    fill_coin = _coin_key(fill.get("coin") or fill.get("symbol"))
    if not fill_coin:
        return None
    for position in positions:
        if not isinstance(position, dict):
            continue
        if _coin_key(position.get("coin") or position.get("symbol")) == fill_coin:
            return position
    return None


def _coin_key(value: Any) -> str:
    text = str(value or "").upper()
    return text.split("-", 1)[0].strip()


def _direction_label(fill: dict[str, Any]) -> str:
    direction = str(fill.get("direction") or "").strip()
    normalized = direction.lower().replace("_", " ")
    if "liquidated" in normalized:
        margin = "全仓" if "cross" in normalized else "逐仓" if "isolated" in normalized else ""
        side = "多单" if "long" in normalized else "空单" if "short" in normalized else ""
        return f"强平{margin}{side}" if margin or side else "强平"
    if "open long" in normalized:
        return "买入开多"
    if "close long" in normalized:
        return "卖出平多"
    if "open short" in normalized:
        return "卖出开空"
    if "close short" in normalized:
        return "买入平空"
    return direction or str(fill.get("side") or "--")


def _price_label(fill: dict[str, Any]) -> str:
    normalized = str(fill.get("direction") or "").strip().lower().replace("_", " ")
    if "liquidated" in normalized:
        return "强平价格"
    if "close" in normalized:
        return "平仓价格"
    if "open" in normalized:
        return "开仓价格"
    return "成交价格"


def _current_position_after_fill(fill: dict[str, Any]) -> float | None:
    raw = fill.get("raw") if isinstance(fill.get("raw"), dict) else {}
    start_value = raw.get("startPosition", fill.get("start_position"))
    if start_value in (None, ""):
        return None
    start = _float(start_value)
    size = abs(_float(fill.get("size")))
    side_code = str(fill.get("side_code") or raw.get("side") or "").upper()
    direction = str(fill.get("direction") or raw.get("dir") or "").strip().lower().replace("_", " ")
    if side_code == "B" or "open long" in direction or "close short" in direction:
        return start + size
    if side_code in {"A", "S"} or "open short" in direction or "close long" in direction:
        return start - size
    return None


def _position_size_label(size: float, coin_value: Any) -> str:
    coin = _coin_key(coin_value) or str(coin_value or "").upper() or "--"
    if abs(size) < 1e-12:
        return f"0 {coin}"
    side = "多" if size > 0 else "空"
    return f"{abs(size):g} {coin} {side}"


def _position_label(position: dict[str, Any]) -> str:
    coin = _coin_key(position.get("coin") or position.get("symbol"))
    signed_size = position.get("signed_size")
    if signed_size not in (None, ""):
        return _position_size_label(_float(signed_size), coin)
    size = _float(position.get("size"))
    if size <= 0:
        return ""
    side = str(position.get("side") or "").strip()
    side_label = "空" if "空" in side or side.lower() == "short" else "多" if "多" in side or side.lower() == "long" else side
    return f"{size:g} {coin or '--'} {side_label}".strip()


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _int(value: Any, fallback: int = 0) -> int:
    try:
        if value in (None, ""):
            return fallback
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _hours(value: int) -> timedelta:
    return timedelta(hours=max(1, int(value)))


def _parse_iso_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _signal_candidate_timestamps(signal: dict[str, Any]) -> list[int]:
    published = _parse_iso_datetime(str(signal.get("published_at") or ""))
    timestamps = [int(published.timestamp())]
    text = f"{signal.get('title') or ''}\n{signal.get('summary') or ''}"
    for match in re.finditer(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text):
        month = _int(match.group(1))
        day = _int(match.group(2))
        if month <= 0 or day <= 0:
            continue
        for hour in (0, 8, 16):
            try:
                candidate = datetime(published.year, month, day, hour, 0, tzinfo=timezone.utc)
            except ValueError:
                continue
            if abs((published - candidate).days) <= 45:
                timestamps.append(int(candidate.timestamp()))
    return list(dict.fromkeys(timestamps))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fill_cursor_key(target_id: str, address: str) -> str:
    return f"whale:hyperliquid:fills:{target_id}:{address.lower()}"


def _extended_cursor_key(target_id: str, address: str) -> str:
    return f"whale:hyperliquid:extended:{target_id}:{address.lower()}"


def _preserve_extended_snapshot_fields(snapshot: Any, old_snapshot: dict[str, Any]) -> None:
    for field in ("fills", "historical_orders", "funding", "ledger_updates", "portfolio"):
        value = old_snapshot.get(field)
        if isinstance(value, list):
            setattr(snapshot, field, value)
    old_account = old_snapshot.get("account_summary") if isinstance(old_snapshot.get("account_summary"), dict) else {}
    if old_account and "fees" in old_account:
        snapshot.account_summary["fees"] = old_account["fees"]


def _load_cursor(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _initial_fill_start_ms(config: dict[str, Any], now_ms: int) -> int | None:
    mode = str(config.get("initial_fill_sync_mode") or "cursor_only")
    if mode == "lookback_3h":
        return now_ms - 3 * 60 * 60 * 1000
    if mode == "today":
        now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        return int(today.timestamp() * 1000)
    return None


def _fill_identity(fill: dict[str, Any]) -> str:
    parts = [
        fill.get("hash"),
        fill.get("trade_id"),
        fill.get("order_id"),
        fill.get("timestamp"),
        fill.get("coin"),
        fill.get("side_code"),
        fill.get("price"),
        fill.get("size"),
    ]
    return ":".join(str(part) for part in parts if part not in (None, ""))


def _fill_time_iso(fill: dict[str, Any]) -> str:
    timestamp = _int(fill.get("timestamp"))
    if timestamp > 0:
        return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _ms_to_iso(timestamp_ms: int) -> str:
    if timestamp_ms > 0:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _is_large_fill(fill: dict[str, Any], config: dict[str, Any]) -> bool:
    notional_threshold = _float(config.get("trade_min_notional_usd"), 100000)
    if _float(fill.get("notional")) >= notional_threshold:
        return True
    raw_thresholds = config.get("trade_coin_thresholds")
    thresholds = raw_thresholds if isinstance(raw_thresholds, dict) else {"ETH": 100, "BTC": 5, "SOL": 10000}
    coin = str(fill.get("coin") or "").upper()
    threshold = _float(thresholds.get(coin), 0)
    return threshold > 0 and abs(_float(fill.get("size"))) >= threshold
