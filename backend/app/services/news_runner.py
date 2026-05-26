from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..core.time import parse_datetime
from .events import EventBus
from .news import DirectStatementAnalyzer, TrumpsTruthRssAdapter, TruthbrushApiAdapter, WhiteHouseGalleryAdapter
from .store import Store
from .translator import Translator


LOGGER = logging.getLogger("market_monitor.news")


class NewsRunner:
    def __init__(self, store: Store, translator: Translator, bus: EventBus, timeout_seconds: int = 20) -> None:
        self.store = store
        self.translator = translator
        self.bus = bus
        self.timeout_seconds = timeout_seconds
        self.analyzer = DirectStatementAnalyzer()

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:  # noqa: BLE001
                LOGGER.exception("新闻轮询失败")
            await asyncio.sleep(self._next_sleep())

    def _next_sleep(self) -> int:
        values = []
        for strategy_id in ("trump_social", "whitehouse"):
            strategy = self.store.get_strategy(strategy_id)
            if strategy and strategy.enabled:
                values.append(int(strategy.config.get("poll_seconds", 1200)))
        return max(60, min(values or [1200]))

    def run_once(self) -> None:
        social = self.store.get_strategy("trump_social")
        whitehouse = self.store.get_strategy("whitehouse")
        adapters = []
        if social and social.enabled:
            if social.config.get("enable_truthbrush"):
                adapters.append(TruthbrushApiAdapter(str(social.config.get("truthsocial_handle", "realDonaldTrump"))))
            if social.config.get("enable_truth_social"):
                adapters.append(
                    TrumpsTruthRssAdapter(
                        str(social.config.get("truth_social_feed_url", "https://www.trumpstruth.org/feed")),
                        self.timeout_seconds,
                    )
                )
        if whitehouse and whitehouse.enabled and whitehouse.config.get("enabled", True):
            adapters.append(
                WhiteHouseGalleryAdapter(
                    str(whitehouse.config.get("whitehouse_gallery_url", "https://www.whitehouse.gov/gallery/")),
                    self.timeout_seconds,
                    [str(item) for item in whitehouse.config.get("include_keywords", []) if str(item).strip()],
                    [str(item) for item in whitehouse.config.get("exclude_keywords", []) if str(item).strip()],
                )
            )

        monitoring_started_at = parse_datetime(
            self.store.state_get("monitoring_started_at_utc") or datetime.now(timezone.utc).isoformat()
        )
        if self.store.state_get("monitoring_started_at_utc") is None:
            self.store.state_set("monitoring_started_at_utc", monitoring_started_at.isoformat())

        immediate_categories = set((social.config if social else {}).get("immediate_categories", []))
        for adapter in adapters:
            try:
                result = adapter.poll()
                for warning in result.warnings:
                    LOGGER.warning("来源告警 source=%s warning=%s", result.source_name, warning)
                for event in result.events:
                    analysis = self.analyzer.analyze(event)
                    event.metadata.update(analysis)
                    categories = set(analysis.get("categories", []))
                    suppress = event.published_at < monitoring_started_at or not bool(categories.intersection(immediate_categories))
                    translated_title = self.translator.translate(event.title)
                    translated_summary = self.translator.translate_summary(event.content or event.title)
                    translated_metadata = self.translator.translate_metadata(
                        event.metadata,
                        source_content=event.content,
                        translated_summary=translated_summary,
                    )
                    status, event_id = self.store.upsert_news_event(
                        source_type=event.source_type,
                        source_name=event.source_name,
                        event_id=event.event_id,
                        published_at_utc=event.published_at.astimezone(timezone.utc).isoformat(),
                        title=event.title,
                        translated_title=translated_title,
                        speaker=event.speaker,
                        content=event.content,
                        translated_summary=translated_summary,
                        url=event.url,
                        raw_hash=event.raw_hash,
                        metadata=translated_metadata,
                        suppress_notification=suppress,
                    )
                    if status == "inserted_new":
                        self.bus.publish_threadsafe("news", {"id": event_id, "title": translated_title or event.title})
                self.store.record_source_success(result.source_name, result.label)
                self.bus.publish_threadsafe("health", {"source": result.source_name, "status": "ok"})
            except Exception as exc:  # noqa: BLE001
                label = getattr(adapter, "label", adapter.source_name)
                self.store.record_source_error(adapter.source_name, label, str(exc))
                self.bus.publish_threadsafe("health", {"source": adapter.source_name, "status": "error"})
                LOGGER.exception("新闻来源失败 source=%s", adapter.source_name)
