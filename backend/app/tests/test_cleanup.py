from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from backend.app.core.database import Database
from backend.app.services.cleanup_worker import CleanupWorker
from backend.app.services.store import Store


NOW = datetime(2026, 5, 26, 4, 31, tzinfo=timezone.utc)
OLD_ALERT = "2026-04-20T00:00:00+00:00"
RECENT_ALERT = "2026-05-20T00:00:00+00:00"
OLD_NEWS = "2026-03-20T00:00:00+00:00"
RECENT_NEWS = "2026-05-20T00:00:00+00:00"
OLD_WHALE = "2026-02-20T00:00:00+00:00"
RECENT_WHALE = "2026-05-20T00:00:00+00:00"


def make_store(tmp_path: Path) -> Store:
    return Store(Database(tmp_path / "test.db", "secret"))


def add_alert(store: Store, dedupe_key: str, created_at: str, *, suppress_notification: bool = True) -> int:
    inserted_id = store.create_alert(
        strategy_id="boll",
        symbol="BTCUSDT",
        interval="15m",
        signal="CLOSE_BELOW_LOWER",
        severity="warning",
        message=dedupe_key,
        detail={"upper": 3, "middle": 2, "lower": 1},
        candle_open_time_ms=1,
        close_price=1.0,
        source="binance_futures",
        source_role="PRIMARY",
        dedupe_key=dedupe_key,
        suppress_notification=suppress_notification,
    )
    assert inserted_id is not None
    store.db.execute("UPDATE alert_events SET created_at = ? WHERE id = ?", (created_at, inserted_id))
    return inserted_id


def add_news(store: Store, event_id: str, published_at: str, *, suppress_notification: bool = True) -> int:
    _, inserted_id = store.upsert_news_event(
        source_type="social",
        source_name="trumps_truth_rss",
        event_id=event_id,
        published_at_utc=published_at,
        title=f"title {event_id}",
        translated_title="",
        speaker="Donald Trump",
        content=f"content {event_id}",
        translated_summary="",
        url=f"https://example.com/{event_id}",
        raw_hash=f"raw-{event_id}",
        metadata={"content_kind": "text"},
        suppress_notification=suppress_notification,
    )
    return inserted_id


def add_whale(store: Store, event_id: str, occurred_at: str) -> None:
    store.db.execute(
        """
        INSERT INTO whale_events (provider, target_id, action_type, summary, payload_json, occurred_at_utc, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("test", "wallet", "transfer", event_id, json.dumps({"id": event_id}), occurred_at, occurred_at),
    )


def test_cleanup_deletes_expired_rows_and_keeps_recent_rows(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    old_alert_id = add_alert(store, "old-alert", OLD_ALERT)
    recent_alert_id = add_alert(store, "recent-alert", RECENT_ALERT)
    old_news_id = add_news(store, "old-news", OLD_NEWS)
    recent_news_id = add_news(store, "recent-news", RECENT_NEWS)
    store.db.execute(
        """
        INSERT INTO news_events (
            source_type, source_name, event_id, published_at_utc, title, translated_title,
            speaker, content, translated_summary, url, raw_hash, content_hash, metadata_json,
            first_seen_utc, last_seen_utc, merged_into_event_pk, notification_sent
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "social",
            "trumps_truth_rss",
            "old-news-merged",
            RECENT_NEWS,
            "merged",
            "",
            "Donald Trump",
            "merged",
            "",
            "https://example.com/old-news-merged",
            "raw-merged",
            "content-merged",
            "{}",
            RECENT_NEWS,
            RECENT_NEWS,
            old_news_id,
            1,
        ),
    )
    add_whale(store, "old-whale", OLD_WHALE)
    add_whale(store, "recent-whale", RECENT_WHALE)

    result = store.cleanup_old_data(
        alert_retention_days=30,
        news_retention_days=60,
        whale_retention_days=90,
        delete_pending_notifications=True,
        vacuum_after_cleanup=False,
        now=NOW,
    )

    assert result["alert_deleted"] == 1
    assert result["news_deleted"] == 2
    assert result["whale_deleted"] == 1
    assert store.db.query_one("SELECT id FROM alert_events WHERE id = ?", (old_alert_id,)) is None
    assert store.db.query_one("SELECT id FROM news_events WHERE id = ?", (old_news_id,)) is None
    assert store.db.query_one("SELECT id FROM alert_events WHERE id = ?", (recent_alert_id,)) is not None
    assert store.db.query_one("SELECT id FROM news_events WHERE id = ?", (recent_news_id,)) is not None
    assert len(store.db.query("SELECT id FROM whale_events")) == 1


def test_cleanup_can_delete_pending_notifications(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_alert(store, "pending-alert", OLD_ALERT, suppress_notification=False)
    add_news(store, "pending-news", OLD_NEWS, suppress_notification=False)

    result = store.cleanup_old_data(
        alert_retention_days=30,
        news_retention_days=60,
        whale_retention_days=90,
        delete_pending_notifications=True,
        vacuum_after_cleanup=False,
        now=NOW,
    )

    assert result["alert_deleted"] == 1
    assert result["news_deleted"] == 1
    assert store.list_pending_alert_notifications() == []
    assert store.list_pending_news_notifications() == []


def test_cleanup_worker_runs_once_per_beijing_date(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_alert(store, "old-alert", OLD_ALERT)
    worker = CleanupWorker(store)

    first = worker.run_once(NOW)
    second = worker.run_once(NOW)

    assert first is not None
    assert first["alert_deleted"] == 1
    assert second is None
    assert store.state_get("cleanup_last_run_date") == "2026-05-26"


def test_cleanup_vacuums_only_when_rows_are_deleted(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    calls: list[str] = []
    original_execute = store.db.execute

    def execute(sql: str, params=()):
        if sql.strip().upper() == "VACUUM":
            calls.append("VACUUM")
        return original_execute(sql, params)

    store.db.execute = execute  # type: ignore[method-assign]
    store.cleanup_old_data(
        alert_retention_days=30,
        news_retention_days=60,
        whale_retention_days=90,
        delete_pending_notifications=True,
        vacuum_after_cleanup=True,
        now=NOW,
    )
    assert calls == []

    add_alert(store, "old-alert", OLD_ALERT)
    result = store.cleanup_old_data(
        alert_retention_days=30,
        news_retention_days=60,
        whale_retention_days=90,
        delete_pending_notifications=True,
        vacuum_after_cleanup=True,
        now=NOW,
    )

    assert result["vacuumed"] is True
    assert calls == ["VACUUM"]
