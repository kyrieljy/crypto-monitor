import json

from backend.app.services.notification_worker import format_alert_notification, format_news_notification


def test_ma_notification_template_uses_robot_format() -> None:
    row = {
        "strategy_id": "ma",
        "symbol": "SOLUSDT",
        "interval": "1h",
        "signal": "MA_CROSS_BELOW",
        "detail_json": json.dumps({"fast_ma": 85.7076, "slow_ma": 85.7248}),
        "candle_open_time_ms": 1779663600000,
        "close_price": 84.94,
        "source": "binance_futures",
        "source_role": "PRIMARY",
        "created_at": "2026-05-24T23:00:04+00:00",
    }

    assert format_alert_notification(row, {"fast_period": 25, "slow_period": 99}) == (
        "[MA预警]\n"
        "标的: SOLUSDT\n"
        "周期: 1h\n"
        "信号: MA25下穿MA99\n"
        "收盘价: 84.9400\n"
        "快线MA: 85.7076\n"
        "慢线MA: 85.7248\n"
        "数据源: 主源 (binance_futures)\n"
        "提醒时间: 2026-05-25 07:00:04 CST"
    )


def test_boll_notification_template_includes_bands() -> None:
    row = {
        "strategy_id": "boll",
        "symbol": "BTCUSDT",
        "interval": "4h",
        "signal": "BOLL_CROSS_ABOVE_UPPER",
        "detail_json": json.dumps({"middle": 77000, "upper": 78000.12345, "lower": 76000.5}),
        "candle_open_time_ms": 1779674400000,
        "close_price": 78100,
        "source": "okx_swap",
        "source_role": "BACKUP",
        "created_at": "2026-05-25T02:00:05+00:00",
    }

    message = format_alert_notification(row, {"period": 20, "stddev": 2})

    assert "[BOLL预警]" in message
    assert "信号: 收盘价上穿BOLL上轨" in message
    assert message.index("BOLL上轨: 78000.1234") < message.index("BOLL中轨: 77000.0000") < message.index("BOLL下轨: 76000.5000")
    assert "K线时间" not in message
    assert "数据源: 灾备 (okx_swap)" in message


def test_news_notification_template_uses_module_title() -> None:
    row = {
        "source_type": "social",
        "source_name": "trumps_truth_rss",
        "metadata_json": json.dumps({"categories": ["personal_or_rhetorical"]}),
        "translated_title": "伟大的达纳·怀特",
        "title": "Dana White",
        "translated_summary": "<p>谢谢你，达纳！</p>",
        "content": "Thanks Dana",
        "speaker": "Donald Trump",
        "url": "https://www.trumpstruth.org/statuses/38762",
        "published_at_utc": "2026-05-25T02:09:59+00:00",
    }

    strategy_id, message = format_news_notification(row)

    assert strategy_id == "trump_social"
    assert message.startswith("[更新] 特朗普言论监控\n")
    assert "时间: 2026-05-25 10:09:59 CST" in message
    assert "来源: social / trumps_truth_rss" in message
    assert "分类: personal_or_rhetorical" in message


def test_media_news_notification_uses_plain_text_and_preview_links() -> None:
    row = {
        "source_type": "social",
        "source_name": "trumps_truth_rss",
        "metadata_json": json.dumps(
            {
                "categories": ["personal_or_rhetorical"],
                "content_kind": "repost",
                "original_url": "https://truthsocial.com/@realDonaldTrump/116635913209650795",
                "media": [{"type": "image", "url": "https://static-assets.example/card.jpg"}],
                "card": {
                    "title": "WomenForTrump (@IStandWithTrump47)",
                    "url": "https://truthsocial.com/users/IStandWithTrump47/statuses/116634820235173843",
                },
            }
        ),
        "translated_title": "",
        "title": "转发：WomenForTrump (@IStandWithTrump47)",
        "translated_summary": "",
        "content": '<p><a href="https://truthsocial.com/users/IStandWithTrump47/statuses/116634820235173843">truthsocial.com</a></p>',
        "speaker": "Donald Trump",
        "url": "https://www.trumpstruth.org/statuses/38775",
        "published_at_utc": "2026-05-25T15:26:36+00:00",
    }

    _, message = format_news_notification(row)

    assert "类型: 转发" in message
    assert "原帖: https://truthsocial.com/@realDonaldTrump/116635913209650795" in message
    assert "媒体预览: https://static-assets.example/card.jpg" in message
    assert "<p>" not in message
    assert "<a " not in message
