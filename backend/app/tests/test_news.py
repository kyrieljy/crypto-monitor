from __future__ import annotations

from pathlib import Path

from backend.app.core.database import Database
from backend.app.services.news import WhiteHouseGalleryAdapter
from backend.app.services.news import _extract_status_detail_metadata, _normalize_truthsocial_post_url, TrumpsTruthRssAdapter
from backend.app.services.store import Store


def test_whitehouse_keyword_filter_matches_include_and_exclude() -> None:
    adapter = WhiteHouseGalleryAdapter(
        "https://www.whitehouse.gov/gallery/",
        20,
        include_keywords=["Trump", "tariff"],
        exclude_keywords=["dinner"],
    )

    assert adapter._matches_keywords("President Donald J. Trump delivers remarks")
    assert adapter._matches_keywords("Tariff announcement")
    assert not adapter._matches_keywords("President Donald J. Trump hosts a dinner")
    assert not adapter._matches_keywords("First Lady visits school")


def test_whitehouse_remarks_page_groups_title_and_date_by_url() -> None:
    html = """
    <a href="/administration/donald-j-trump/">About President Trump</a>
    <a href="/remarks/">Remarks</a>
    <a href="/videos/president-trump-participates-in-a-rose-garden-club-dinner-with-american-farmers/">21:06</a>
    <a href="/videos/president-trump-participates-in-a-rose-garden-club-dinner-with-american-farmers/">
      President Trump Participates in a Rose Garden Club Dinner with American Farmers
    </a>
    <a href="/videos/president-trump-participates-in-a-rose-garden-club-dinner-with-american-farmers/">June 26, 2026</a>
    """

    def fetcher(url: str, timeout: int) -> str:
        return html

    events = WhiteHouseGalleryAdapter(
        "https://www.whitehouse.gov/remarks/",
        20,
        include_keywords=["Trump"],
        fetcher=fetcher,
    ).poll().events

    assert len(events) == 1
    assert events[0].url == "https://www.whitehouse.gov/videos/president-trump-participates-in-a-rose-garden-club-dinner-with-american-farmers/"
    assert events[0].published_at.date().isoformat() == "2026-06-26"
    assert events[0].title == "President Trump Participates in a Rose Garden Club Dinner with American Farmers"
    assert events[0].metadata["date_text"] == "June 26, 2026"


def test_truth_rss_media_post_enriches_original_and_card_metadata() -> None:
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:truth="https://truthsocial.com/ns">
      <channel>
        <item>
          <title><![CDATA[[No Title] - Post from May 25, 2026]]></title>
          <link>https://www.trumpstruth.org/statuses/38775</link>
          <description><![CDATA[<p><a href="https://truthsocial.com/users/IStandWithTrump47/statuses/116634820235173843">truthsocial.com/users/IStandWithTrump47/statuses/116634820235173843</a></p>]]></description>
          <guid>https://www.trumpstruth.org/statuses/38775</guid>
          <pubDate>Mon, 25 May 2026 15:26:36 +0000</pubDate>
          <truth:originalUrl>https://truthsocial.com/@realDonaldTrump/116635913209650795</truth:originalUrl>
          <truth:originalId>116635913209650795</truth:originalId>
        </item>
      </channel>
    </rss>
    """
    detail = """
    <html><head>
      <meta property="og:image" content="https://truth-archive.example/38775.jpg">
    </head><body>
      <a href="https://truthsocial.com/users/IStandWithTrump47/statuses/116634820235173843" class="status-card">
        <div class="status-card__media"><img src="https://static-assets.example/card.jpg" class="status-card__image"></div>
        <div class="status-card__title">WomenForTrump (@IStandWithTrump47)</div>
      </a>
    </body></html>
    """

    def fetcher(url: str, timeout: int) -> str:
        return detail if url.endswith("/statuses/38775") else feed

    events = TrumpsTruthRssAdapter("https://www.trumpstruth.org/feed", 20, fetcher=fetcher).poll().events

    assert len(events) == 1
    event = events[0]
    assert event.metadata["original_url"] == "https://truthsocial.com/@realDonaldTrump/posts/116635913209650795"
    assert event.metadata["original_id"] == "116635913209650795"
    assert event.metadata["content_kind"] == "repost"
    assert event.metadata["card"]["image_url"] == "https://static-assets.example/card.jpg"
    assert event.metadata["card"]["media_type"] == "image"
    assert "media" not in event.metadata
    assert event.content == "转发图片内容，点击查看原帖。"
    assert "<p>" not in event.content


def test_truth_rss_detail_thumbnail_classifies_as_image() -> None:
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:truth="https://truthsocial.com/ns">
      <channel>
        <item>
          <title><![CDATA[[No Title] - Post from May 26, 2026]]></title>
          <link>https://www.trumpstruth.org/statuses/38781</link>
          <description><![CDATA[]]></description>
          <guid>https://www.trumpstruth.org/statuses/38781</guid>
          <pubDate>Tue, 26 May 2026 00:09:00 +0000</pubDate>
          <truth:originalId>116637970897874082</truth:originalId>
        </item>
      </channel>
    </rss>
    """
    detail = """
    <div class="status__attachments "></div>
    <a href="https://www.trumpstruth.org/images/image-placeholder-icon.png" data-fancybox>
      <img src="https://static-assets.example/media_attachments/files/116/637/970/718/115/837/small/5bf6457f6dda210a.jpg"
           class="status-details-attachment__thumbnail status-details-attachment__thumbnail--image">
    </a>
    """

    def fetcher(url: str, timeout: int) -> str:
        return detail if url.endswith("/statuses/38781") else feed

    event = TrumpsTruthRssAdapter("https://www.trumpstruth.org/feed", 20, fetcher=fetcher).poll().events[0]

    assert event.metadata["content_kind"] == "image"
    assert event.metadata["original_url"] == "https://truthsocial.com/@realDonaldTrump/posts/116637970897874082"
    assert event.metadata["media"][0]["source"] == "attachment-detail"


def test_status_detail_extracts_attachment_and_card_image_without_og_preview() -> None:
    metadata = _extract_status_detail_metadata(
        """
        <meta property="og:image" content="https://truth-archive.example/social_previews/38775/38775.jpg">
        <a href="https://truth-archive.example/attachments/1/original.jpg" class="status-attachment__link">
          <img src="https://static-assets.example/media_attachments/files/1/small/original.jpg" class="status-attachment__image">
        </a>
        <a href="https://truthsocial.com/users/example/statuses/1" class="status-card">
          <img src="https://static-assets.example/card.jpg" class="status-card__image">
          <div class="status-card__title">Linked status title</div>
          <div class="status-card__description">Linked status description</div>
        </a>
        """
    )

    assert metadata["card"]["title"] == "Linked status title"
    assert metadata["card"]["description"] == "Linked status description"
    assert metadata["card"]["image_url"] == "https://static-assets.example/card.jpg"
    assert metadata["card"]["media_type"] == "image"
    assert metadata["media"] == [
        {
            "type": "image",
            "url": "https://truth-archive.example/attachments/1/original.jpg",
            "thumbnail_url": "https://static-assets.example/media_attachments/files/1/small/original.jpg",
            "source": "attachment",
        }
    ]


def test_status_detail_uses_detail_thumbnail_when_body_attachment_missing() -> None:
    metadata = _extract_status_detail_metadata(
        """
        <div class="status__attachments "></div>
        <a href="https://www.trumpstruth.org/images/image-placeholder-icon.png" data-fancybox>
          <img src="https://static-assets.example/media_attachments/files/116/637/970/718/115/837/small/5bf6457f6dda210a.jpg"
               class="status-details-attachment__thumbnail status-details-attachment__thumbnail--image">
        </a>
        """
    )

    assert metadata["media"] == [
        {
            "type": "image",
            "url": "https://static-assets.example/media_attachments/files/116/637/970/718/115/837/small/5bf6457f6dda210a.jpg",
            "source": "attachment-detail",
        }
    ]


def test_status_detail_prefers_body_attachment_over_detail_thumbnail() -> None:
    metadata = _extract_status_detail_metadata(
        """
        <a href="https://truth-archive.example/attachments/1/original.jpg" class="status-attachment__link">
          <img src="https://static-assets.example/media_attachments/files/1/small/original.jpg" class="status-attachment__image">
        </a>
        <a href="https://www.trumpstruth.org/images/image-placeholder-icon.png" data-fancybox>
          <img src="https://static-assets.example/media_attachments/files/1/small/duplicate.jpg"
               class="status-details-attachment__thumbnail status-details-attachment__thumbnail--image">
        </a>
        """
    )

    assert metadata["media"] == [
        {
            "type": "image",
            "url": "https://truth-archive.example/attachments/1/original.jpg",
            "thumbnail_url": "https://static-assets.example/media_attachments/files/1/small/original.jpg",
            "source": "attachment",
        }
    ]


def test_truthsocial_original_url_is_normalized_to_posts_path() -> None:
    assert (
        _normalize_truthsocial_post_url("https://truthsocial.com/@realDonaldTrump/116637923930386496")
        == "https://truthsocial.com/@realDonaldTrump/posts/116637923930386496"
    )
    assert (
        _normalize_truthsocial_post_url("https://truthsocial.com/@realDonaldTrump/posts/116637923930386496")
        == "https://truthsocial.com/@realDonaldTrump/posts/116637923930386496"
    )
    assert (
        _normalize_truthsocial_post_url("https://truthsocial.com/users/realDonaldTrump/statuses/116637923930386496")
        == "https://truthsocial.com/@realDonaldTrump/posts/116637923930386496"
    )


def test_truth_rss_repost_uses_card_description_for_translatable_summary() -> None:
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title><![CDATA[[No Title] - Post from May 25, 2026]]></title>
          <link>https://www.trumpstruth.org/statuses/38800</link>
          <description><![CDATA[<p><a href="https://truthsocial.com/users/example/statuses/1">truthsocial.com/users/example/statuses/1</a></p>]]></description>
          <guid>https://www.trumpstruth.org/statuses/38800</guid>
          <pubDate>Mon, 25 May 2026 16:26:36 +0000</pubDate>
        </item>
      </channel>
    </rss>
    """
    detail = """
    <a href="https://truthsocial.com/users/example/statuses/1" class="status-card">
      <div class="status-card__title">Example account (@example)</div>
      <div class="status-card__description">The original repost text that should be summarized.</div>
    </a>
    """

    def fetcher(url: str, timeout: int) -> str:
        return detail if url.endswith("/statuses/38800") else feed

    event = TrumpsTruthRssAdapter("https://www.trumpstruth.org/feed", 20, fetcher=fetcher).poll().events[0]

    assert event.metadata["content_kind"] == "repost"
    assert event.content == "The original repost text that should be summarized."


def test_truth_rss_external_card_is_classified_as_link() -> None:
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title><![CDATA[[No Title] - Post from May 26, 2026]]></title>
          <link>https://www.trumpstruth.org/statuses/38900</link>
          <description><![CDATA[<p><a href="https://example.com/story">https://example.com/story</a></p>]]></description>
          <guid>https://www.trumpstruth.org/statuses/38900</guid>
          <pubDate>Tue, 26 May 2026 01:26:36 +0000</pubDate>
        </item>
      </channel>
    </rss>
    """
    detail = """
    <a href="https://example.com/story" class="status-card">
      <img src="https://static-assets.example/card.jpg" class="status-card__image">
      <div class="status-card__title">External story title</div>
      <div class="status-card__description">External story description.</div>
    </a>
    """

    def fetcher(url: str, timeout: int) -> str:
        return detail if url.endswith("/statuses/38900") else feed

    event = TrumpsTruthRssAdapter("https://www.trumpstruth.org/feed", 20, fetcher=fetcher).poll().events[0]

    assert event.metadata["content_kind"] == "link"
    assert event.metadata["card"]["image_url"] == "https://static-assets.example/card.jpg"
    assert "media" not in event.metadata


def test_media_news_payloads_are_not_merged_by_no_title_similarity(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    for index in range(2):
        store.upsert_news_event(
            source_type="social",
            source_name="trumps_truth_rss",
            event_id=f"https://www.trumpstruth.org/statuses/media-{index}",
            published_at_utc=f"2026-05-25T15:2{index}:00+00:00",
            title="图片内容 - 2026-05-25",
            translated_title="",
            speaker="Donald Trump",
            content="图片内容，点击查看原帖。",
            translated_summary="",
            url=f"https://www.trumpstruth.org/statuses/media-{index}",
            raw_hash=f"raw-{index}",
            metadata={
                "content_kind": "image",
                "media": [{"type": "image", "url": f"https://example.com/{index}.jpg"}],
            },
            suppress_notification=False,
        )

    assert len(store.list_news(10)) == 2
