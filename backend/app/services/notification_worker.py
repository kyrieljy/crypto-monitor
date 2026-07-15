from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any

from .notifiers import NotificationService
from .store import Store


LOGGER = logging.getLogger("market_monitor.notifications")
CST = timezone(timedelta(hours=8), "CST")


def _format_cst(value: Any) -> str:
    if value is None or value == "":
        return "--"
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        else:
            raw = str(value).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S CST")
    except Exception:  # noqa: BLE001
        return str(value)


def _format_number(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:  # noqa: BLE001
        return "--"


def _plain_text(value: Any) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _source_label(source_role: Any, source: Any) -> str:
    role = str(source_role or "").upper()
    label = {
        "PRIMARY": "主源",
        "BACKUP": "灾备",
        "FALLBACK": "灾备",
    }.get(role, role or "未知")
    return f"{label} ({source or '--'})"


def _format_money(value: Any, digits: int = 2) -> str:
    try:
        return f"${float(value):,.{digits}f}"
    except Exception:  # noqa: BLE001
        return "--"


def _format_amount(value: Any) -> str:
    try:
        number = float(value)
        return f"{number:,.4f}".rstrip("0").rstrip(".")
    except Exception:  # noqa: BLE001
        return "--"


def _mask_address(value: Any) -> str:
    text = str(value or "")
    if len(text) >= 12 and text.startswith("0x"):
        return f"{text[:6]}...{text[-4:]}"
    return text or "--"


def _whale_direction_label(fill: dict[str, Any], payload: dict[str, Any]) -> str:
    label = str(payload.get("direction_label") or fill.get("direction_label") or "").strip()
    normalized_label = label.lower().replace("_", " ")
    if label and "liquidated" not in normalized_label:
        return label
    direction = str(fill.get("direction") or "").strip()
    normalized = (direction or label).lower().replace("_", " ")
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


def _whale_price_label(fill: dict[str, Any], payload: dict[str, Any]) -> str:
    label = str(payload.get("price_label") or fill.get("price_label") or "").strip()
    if label:
        return label
    normalized = str(fill.get("direction") or "").strip().lower().replace("_", " ")
    if "liquidated" in normalized:
        return "强平价格"
    if "close" in normalized:
        return "平仓价格"
    if "open" in normalized:
        return "开仓价格"
    return "成交价格"


def _format_leverage(value: Any, margin_mode: Any = None) -> str:
    if value in (None, ""):
        return "--"
    try:
        number = float(value)
        leverage = f"{number:g}x"
    except Exception:  # noqa: BLE001
        leverage = str(value)
    mode = str(margin_mode or "").strip()
    return f"{leverage} {mode}".strip()


def _detail(row: Any) -> dict[str, Any]:
    raw = row["detail_json"] if row["detail_json"] else "{}"
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}


def _content_kind_label(kind: Any) -> str:
    return {
        "image": "图片",
        "video": "视频",
        "repost": "转发",
        "link": "链接",
        "media": "媒体",
        "text": "文字",
    }.get(str(kind or "text"), "文字")


def _first_media_url(metadata: dict[str, Any]) -> str:
    media = metadata.get("media")
    if isinstance(media, list):
        for item in media:
            if isinstance(item, dict):
                url = str(item.get("url") or item.get("thumbnail_url") or "").strip()
                if url:
                    return url
    card = metadata.get("card")
    if isinstance(card, dict):
        return str(card.get("image_url") or "").strip()
    return ""


def format_alert_notification(row: Any, strategy_config: dict[str, Any] | None = None) -> str:
    strategy_id = str(row["strategy_id"])
    detail = _detail(row)
    config = strategy_config or {}
    common = [
        f"标的: {row['symbol']}",
        f"周期: {row['interval']}",
    ]

    if strategy_id == "ma":
        fast_period = int(config.get("fast_period", 25))
        slow_period = int(config.get("slow_period", 99))
        direction = "上穿" if row["signal"] == "MA_CROSS_ABOVE" else "下穿"
        lines = [
            "[MA预警]",
            *common,
            f"信号: MA{fast_period}{direction}MA{slow_period}",
            f"收盘价: {_format_number(row['close_price'])}",
            f"快线MA: {_format_number(detail.get('fast_ma'))}",
            f"慢线MA: {_format_number(detail.get('slow_ma'))}",
        ]
    elif strategy_id == "kdj":
        signal = "J上穿K" if row["signal"] == "J_CROSS_ABOVE_K" else "J下穿K"
        lines = [
            "[KDJ预警]",
            *common,
            f"信号: {signal}",
            f"收盘价: {_format_number(row['close_price'])}",
            f"K: {_format_number(detail.get('K'))}",
            f"D: {_format_number(detail.get('D'))}",
            f"J: {_format_number(detail.get('J'))}",
        ]
    elif strategy_id == "boll":
        signal = "收盘价上穿BOLL上轨" if row["signal"] == "BOLL_CROSS_ABOVE_UPPER" else "收盘价下穿BOLL下轨"
        lines = [
            "[BOLL预警]",
            *common,
            f"信号: {signal}",
            f"收盘价: {_format_number(row['close_price'])}",
            f"BOLL上轨: {_format_number(detail.get('upper'))}",
            f"BOLL中轨: {_format_number(detail.get('middle'))}",
            f"BOLL下轨: {_format_number(detail.get('lower'))}",
        ]
    elif strategy_id == "boll_ma_cross":
        boll_period = int(detail.get("boll_period") or config.get("boll_period", 20))
        ma_period = int(detail.get("ma_period") or config.get("ma_period", 99))
        direction = "上穿" if row["signal"] == "BOLL_MIDDLE_CROSS_ABOVE_MA" else "下穿"
        lines = [
            "[BOLL中轨/MA预警]",
            *common,
            f"信号: BOLL中轨{direction}MA{ma_period}",
            f"收盘价: {_format_number(row['close_price'])}",
            f"BOLL中轨({boll_period}): {_format_number(detail.get('boll_middle'))}",
            f"MA{ma_period}: {_format_number(detail.get('ma'))}",
        ]
    else:
        lines = [
            f"[{strategy_id.upper()}预警]",
            *common,
            f"信号: {row['signal']}",
            f"收盘价: {_format_number(row['close_price'])}",
        ]

    lines.extend(
        [
            f"数据源: {_source_label(row['source_role'], row['source'])}",
            f"提醒时间: {_format_cst(row['created_at'])}",
        ]
    )
    return "\n".join(lines)


def format_news_notification(row: Any) -> tuple[str, str]:
    metadata = json.loads(row["metadata_json"] or "{}")
    raw_categories = metadata.get("categories", [])
    if isinstance(raw_categories, list):
        categories = ", ".join(str(item) for item in raw_categories if str(item).strip()) or "未分类"
    else:
        categories = str(raw_categories or "未分类")

    title = _plain_text(row["translated_title"] or row["title"] or "--")
    summary = _plain_text(row["translated_summary"] or row["content"] or "--")
    content_kind = _content_kind_label(metadata.get("content_kind"))
    original_url = str(metadata.get("original_url") or "").strip()
    media_url = _first_media_url(metadata)
    card = metadata.get("card")
    strategy_id = "whitehouse" if row["source_name"] == "whitehouse_gallery" else "trump_social"
    module_title = "白宫发言新闻" if strategy_id == "whitehouse" else "特朗普言论监控"
    lines = [
        f"[更新] {module_title}",
        f"时间: {_format_cst(row['published_at_utc'])}",
        f"来源: {row['source_type']} / {row['source_name']}",
        f"分类: {categories}",
        f"类型: {content_kind}",
        f"标题: {title}",
        f"人物: {row['speaker'] or '--'}",
        f"摘要: {summary}",
        f"链接: {row['url'] or '--'}",
    ]
    if original_url and original_url != row["url"]:
        lines.append(f"原帖: {original_url}")
    if isinstance(card, dict) and card.get("url"):
        card_title = _plain_text(card.get("translated_title") or card.get("title") or card.get("url"))
        lines.append(f"卡片: {card_title} ({card.get('url')})")
    if media_url:
        lines.append(f"媒体预览: {media_url}")
    message = "\n".join(lines)
    return strategy_id, message


def format_whale_notification(row: Any) -> str:
    payload = json.loads(row["payload_json"] or "{}")
    if row["provider"] == "blackrock_free":
        return _format_blackrock_free_notification(row, payload)
    fill = payload.get("fill") if isinstance(payload.get("fill"), dict) else {}
    label = str(payload.get("target_label") or row["target_label"] or row["target_id"])
    coin = str(fill.get("coin") or "--")
    price_label = _whale_price_label(fill, payload)
    fee = fill.get("fee")
    fee_token = str(fill.get("fee_token") or "USDC")
    closed_pnl = fill.get("closed_pnl")
    lines = [
        "[Hyperliquid成交提醒]",
        f"对象: {label}",
        f"币种: {coin}",
        f"仓位动作: {_whale_direction_label(fill, payload)}",
        f"数量: {_format_amount(fill.get('size'))} {coin}",
        f"当前仓位: {payload.get('current_position_label') or '--'}",
        f"{price_label}: {_format_money(fill.get('price'))}",
        f"杠杆: {_format_leverage(payload.get('position_leverage'), payload.get('position_margin_mode'))}",
        f"成交额: {_format_money(fill.get('notional'), 0)}",
    ]
    if fee not in (None, ""):
        lines.append(f"手续费: {_format_amount(fee)} {fee_token}")
    if closed_pnl not in (None, ""):
        lines.append(f"已实现盈亏: {_format_money(closed_pnl)}")
    lines.append(f"时间: {_format_cst(row['occurred_at_utc'])}")
    return "\n".join(lines)


def _format_blackrock_free_notification(row: Any, payload: dict[str, Any]) -> str:
    label = str(payload.get("target_label") or row["target_label"] or row["target_id"])
    lines = [
        "[IBIT免费监控提醒]",
        f"对象: {label}",
        f"类型: {row['action_type']}",
        f"摘要: {row['summary']}",
        f"来源: {payload.get('source') or '--'}",
        f"时间: {_format_cst(row['occurred_at_utc'])}",
    ]
    if payload.get("flow_usd") not in (None, ""):
        lines.append(f"ETF资金流: {_format_money(payload.get('flow_usd'), 0)}")
    signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
    if signal:
        lines.append(f"新闻: {signal.get('title') or '--'}")
        lines.append(f"置信度: {float(signal.get('confidence') or 0) * 100:.0f}%")
        reasons = signal.get("reasons") if isinstance(signal.get("reasons"), list) else []
        if reasons:
            lines.append(f"理由: {'；'.join(str(item) for item in reasons[:5])}")
        addresses = signal.get("candidate_addresses") if isinstance(signal.get("candidate_addresses"), list) else []
        if addresses:
            lines.append(f"疑似地址: {', '.join(str(item) for item in addresses[:5])}")
        txids = signal.get("txids") if isinstance(signal.get("txids"), list) else []
        if txids:
            lines.append(f"TxID: {', '.join(str(item) for item in txids[:3])}")
        if signal.get("url"):
            lines.append(f"新闻链接: {signal.get('url')}")
    transfer = payload.get("transfer") if isinstance(payload.get("transfer"), dict) else {}
    if transfer:
        lines.append(f"BTC转出: {_format_amount(transfer.get('amount_btc'))} BTC")
        if transfer.get("txid"):
            lines.append(f"TxID: {transfer.get('txid')}")
        if transfer.get("source_url"):
            lines.append(f"链接: {transfer.get('source_url')}")
    operation = payload.get("operation") if isinstance(payload.get("operation"), dict) else {}
    if operation:
        lines.append(f"BTC操作: {operation.get('behavior') or operation.get('direction') or '--'} {_format_amount(operation.get('amount_btc'))} BTC")
        lines.append(f"净额: {_format_amount(operation.get('net_btc'))} BTC")
        if operation.get("address"):
            lines.append(f"地址: {operation.get('address')}")
        if operation.get("txid"):
            lines.append(f"TxID: {operation.get('txid')}")
        if operation.get("source_url"):
            lines.append(f"链接: {operation.get('source_url')}")
    return "\n".join(lines)


class NotificationWorker:
    def __init__(self, store: Store, notification_service: NotificationService) -> None:
        self.store = store
        self.notification_service = notification_service

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:  # noqa: BLE001
                LOGGER.exception("通知重试轮询失败")
            await asyncio.sleep(15)

    def run_once(self) -> None:
        for row in self.store.list_pending_alert_notifications():
            try:
                strategy = self.store.get_strategy(row["strategy_id"])
                notification_text = format_alert_notification(row, strategy.config if strategy else None)
                ok, dry_run, message = self.notification_service.send_strategy_message(row["strategy_id"], notification_text)
                self.store.mark_alert_notification(int(row["id"]), ok=ok, error=None if ok else message)
                LOGGER.info("告警通知处理 id=%s ok=%s dry_run=%s message=%s", row["id"], ok, dry_run, message)
            except Exception as exc:  # noqa: BLE001
                self.store.mark_alert_notification(int(row["id"]), ok=False, error=str(exc))
                LOGGER.exception("告警通知失败 id=%s", row["id"])

        for row in self.store.list_pending_news_notifications():
            try:
                strategy_id, message = format_news_notification(row)
                ok, dry_run, result_message = self.notification_service.send_strategy_message(strategy_id, message)
                self.store.mark_news_notification(int(row["id"]), ok=ok, error=None if ok else result_message)
                LOGGER.info("新闻通知处理 id=%s ok=%s dry_run=%s", row["id"], ok, dry_run)
            except Exception as exc:  # noqa: BLE001
                self.store.mark_news_notification(int(row["id"]), ok=False, error=str(exc))
                LOGGER.exception("新闻通知失败 id=%s", row["id"])

        for row in self.store.list_pending_whale_notifications():
            try:
                message = format_whale_notification(row)
                ok, dry_run, result_message = self.notification_service.send_whale_message(row, message)
                self.store.mark_whale_notification(int(row["id"]), ok=ok, error=None if ok else result_message)
                LOGGER.info("巨鲸通知处理 id=%s ok=%s dry_run=%s", row["id"], ok, dry_run)
            except Exception as exc:  # noqa: BLE001
                self.store.mark_whale_notification(int(row["id"]), ok=False, error=str(exc))
                LOGGER.exception("巨鲸通知失败 id=%s", row["id"])
