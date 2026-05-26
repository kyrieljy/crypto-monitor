from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Callable
from urllib import parse, request
from xml.etree import ElementTree as ET

from ..core.text import normalize_text, normalize_whitespace, raw_payload_hash
from ..core.time import parse_datetime


@dataclass(slots=True)
class SpeechEvent:
    source_type: str
    source_name: str
    event_id: str
    published_at: datetime
    title: str
    speaker: str
    content: str
    url: str
    raw_hash: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class SourcePollResult:
    source_name: str
    label: str
    events: list[SpeechEvent]
    warnings: list[str] = field(default_factory=list)


def _http_get(url: str, timeout_seconds: int, headers: dict[str, str] | None = None) -> str:
    req = request.Request(url, headers=headers or {"User-Agent": "market-monitor/1.0"}, method="GET")
    with request.urlopen(req, timeout=timeout_seconds) as resp:
        return resp.read().decode("utf-8", errors="replace")


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li"}:
            self.parts.append(" ")


class _DescriptionLinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._href = dict(attrs).get("href") or ""
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        text = normalize_whitespace(unescape("".join(self._text_parts)))
        if self._href:
            self.links.append({"url": self._href, "text": text or self._href})
        self._href = None
        self._text_parts = []


class _StatusDetailParser(HTMLParser):
    _void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.card: dict[str, str] = {}
        self.media: list[dict[str, str]] = []
        self.detail_media: list[dict[str, str]] = []
        self.original_url = ""
        self._stack: list[dict[str, object]] = []
        self._card_title_parts: list[str] = []
        self._card_description_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        classes = set(attr.get("class", "").split())
        parent = self._stack[-1] if self._stack else {}
        context = {
            "in_card": bool(parent.get("in_card")) or "status-card" in classes,
            "in_video": bool(parent.get("in_video")) or tag == "video",
            "field": parent.get("field"),
            "attachment_url": parent.get("attachment_url"),
        }
        if "status-card__title" in classes:
            context["field"] = "card_title"
        elif "status-card__description" in classes:
            context["field"] = "card_description"

        if tag == "meta":
            key = attr.get("property") or attr.get("name")
            content = attr.get("content")
            if key and content:
                self.meta[key] = content
        elif tag == "a" and "status-card" in classes and attr.get("href"):
            self.card["url"] = attr["href"]
        elif tag == "a" and "status__external-link" in classes and attr.get("href"):
            self.original_url = _normalize_truthsocial_post_url(attr["href"])
        elif tag == "a" and attr.get("href") and (
            "status-attachment__link" in classes or "status-details-attachment__link" in classes or "data-fancybox" in attr
        ):
            context["attachment_url"] = attr["href"]
        elif tag == "img":
            self._handle_image(attr, classes, bool(context["in_card"]), str(context.get("attachment_url") or ""))
        elif tag == "video":
            self._handle_video(attr, bool(context["in_card"]))
        elif tag == "source" and parent.get("in_video") and attr.get("src"):
            if parent.get("in_card"):
                self.card["media_type"] = "video"
            else:
                self._add_media("video", attr["src"], thumbnail_url=self._last_video_thumbnail())

        if tag not in self._void_tags:
            self._stack.append(context)

    def handle_data(self, data: str) -> None:
        if not self._stack:
            return
        field = self._stack[-1].get("field")
        if field == "card_title":
            self._card_title_parts.append(data)
        elif field == "card_description":
            self._card_description_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._stack:
            self._stack.pop()

    def result(self) -> dict[str, object]:
        title = normalize_whitespace(unescape("".join(self._card_title_parts)))
        description = normalize_whitespace(unescape("".join(self._card_description_parts)))
        if title:
            self.card["title"] = title
        if description:
            self.card["description"] = description
        result: dict[str, object] = {}
        if self.original_url:
            result["original_url"] = self.original_url
        media = self.media or self.detail_media
        if media:
            result["media"] = _dedupe_media(media)
        if self.card:
            result["card"] = self.card
        return result

    def _handle_image(self, attr: dict[str, str], classes: set[str], in_card: bool, attachment_url: str) -> None:
        src = attr.get("src")
        if not src:
            return
        if in_card or "status-card__image" in classes:
            self.card["image_url"] = src
            self.card.setdefault("media_type", "image")
            return
        if "status-details-attachment__thumbnail" in classes:
            media_url = attachment_url if attachment_url and not _is_placeholder_image_url(attachment_url) else src
            thumbnail_url = src if media_url != src else None
            self._add_detail_media("image", media_url, thumbnail_url=thumbnail_url)
            return
        if "status-attachment__image" in classes:
            media_url = attachment_url or src
            thumbnail_url = src if attachment_url and attachment_url != src else None
            self._add_media("image", media_url, thumbnail_url=thumbnail_url, source="attachment")
            return
        if any("media" in item or item.startswith("status__") for item in classes):
            self._add_media("image", src, source="status")

    def _handle_video(self, attr: dict[str, str], in_card: bool) -> None:
        src = attr.get("src")
        poster = attr.get("poster")
        if in_card:
            self.card["media_type"] = "video"
            if poster:
                self.card.setdefault("image_url", poster)
            return
        if src or poster:
            self._add_media("video", src or poster or "", thumbnail_url=poster, source="status")

    def _last_video_thumbnail(self) -> str | None:
        for item in reversed(self.media):
            if item.get("type") == "video":
                return item.get("thumbnail_url")
        return None

    def _add_media(self, media_type: str, url: str, *, thumbnail_url: str | None = None, source: str | None = None) -> None:
        if not url:
            return
        if _is_social_preview_url(url):
            return
        item = {"type": media_type, "url": url}
        if thumbnail_url and not _is_social_preview_url(thumbnail_url):
            item["thumbnail_url"] = thumbnail_url
        if source:
            item["source"] = source
        self.media.append(item)

    def _add_detail_media(self, media_type: str, url: str, *, thumbnail_url: str | None = None) -> None:
        if not url:
            return
        if _is_social_preview_url(url):
            return
        item = {"type": media_type, "url": url, "source": "attachment-detail"}
        if thumbnail_url and not _is_social_preview_url(thumbnail_url):
            item["thumbnail_url"] = thumbnail_url
        self.detail_media.append(item)


def _html_to_text(value: str) -> str:
    parser = _HtmlTextExtractor()
    try:
        parser.feed(value or "")
    except Exception:  # noqa: BLE001
        return normalize_whitespace(unescape(re.sub(r"<[^>]+>", " ", value or "")))
    return normalize_whitespace(unescape(" ".join(parser.parts)))


def _extract_links(value: str) -> list[dict[str, str]]:
    parser = _DescriptionLinkExtractor()
    try:
        parser.feed(value or "")
    except Exception:  # noqa: BLE001
        return []
    seen: set[str] = set()
    links: list[dict[str, str]] = []
    for item in parser.links:
        url = item.get("url", "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        links.append({"url": url, "text": item.get("text", url)})
    return links


def _extract_status_detail_metadata(html: str) -> dict[str, object]:
    parser = _StatusDetailParser()
    parser.feed(html or "")
    return parser.result()


def _is_social_preview_url(url: str) -> bool:
    return "/social_previews/" in url.lower()


def _is_placeholder_image_url(url: str) -> bool:
    return url.lower().endswith("/images/image-placeholder-icon.png")


def _truthsocial_post_url_from_id(post_id: str, handle: str = "realDonaldTrump") -> str:
    value = post_id.strip()
    if not value or not value.isdigit():
        return ""
    return f"https://truthsocial.com/@{handle.lstrip('@')}/posts/{value}"


def _normalize_truthsocial_post_url(url: str) -> str:
    value = url.strip()
    if not value:
        return ""
    parsed = parse.urlparse(value)
    host = parsed.netloc.lower()
    if host not in {"truthsocial.com", "www.truthsocial.com"}:
        return value
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 2 and parts[0].startswith("@") and parts[1].isdigit():
        return parse.urlunparse(parsed._replace(path=f"/{parts[0]}/posts/{parts[1]}"))
    if len(parts) == 4 and parts[0] == "users" and parts[2] == "statuses" and parts[3].isdigit():
        return parse.urlunparse(parsed._replace(path=f"/@{parts[1]}/posts/{parts[3]}"))
    return value


def _dedupe_media(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in items:
        url = item.get("url") or item.get("thumbnail_url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(item)
    return result


def _find_child_text(item: ET.Element, local_name: str) -> str:
    for child in item:
        tag = child.tag
        if tag == local_name or tag.endswith(f"}}{local_name}"):
            return (child.text or "").strip()
    return ""


def _is_no_title(title: str) -> bool:
    return title.strip().lower().startswith("[no title]")


def _is_truthsocial_status_link(url: str) -> bool:
    lower = url.lower()
    return "truthsocial.com/" in lower and "/statuses/" in lower


def _is_link_only(content: str, links: list[dict[str, str]]) -> bool:
    if not links:
        return False
    cleaned = normalize_whitespace(content).lower()
    if not cleaned:
        return True
    for item in links:
        url = item["url"].lower()
        text = item.get("text", "").lower()
        if cleaned in {url, text} or cleaned.replace("https://", "").replace("http://", "") in url:
            return True
    return False


def _needs_detail_fetch(title: str, content: str, links: list[dict[str, str]]) -> bool:
    if _is_no_title(title) or not content.strip():
        return True
    if _is_link_only(content, links):
        return True
    return content.strip().lower().startswith("rt:")


def _classify_content(title: str, content: str, links: list[dict[str, str]], metadata: dict[str, object]) -> str:
    media = metadata.get("media")
    card = metadata.get("card")
    has_media = isinstance(media, list) and bool(media)
    has_video = has_media and any(isinstance(item, dict) and item.get("type") == "video" for item in media)
    has_card = isinstance(card, dict) and bool(card)
    has_truth_status_link = any(_is_truthsocial_status_link(item.get("url", "")) for item in links)
    if content.strip().lower().startswith("rt:") or has_truth_status_link:
        return "repost"
    if has_video:
        return "video"
    if has_media:
        return "image"
    if _is_link_only(content, links):
        return "link"
    if has_card:
        return "link"
    if _is_no_title(title):
        return "media"
    return "text"


def _content_kind_label(kind: str) -> str:
    return {
        "image": "图片内容",
        "video": "视频内容",
        "repost": "转发内容",
        "link": "链接内容",
        "media": "媒体内容",
    }.get(kind, "文字内容")


def _display_title(title: str, kind: str, metadata: dict[str, object], published_at: datetime) -> str:
    if not _is_no_title(title):
        return title
    card = metadata.get("card")
    if isinstance(card, dict) and str(card.get("title") or "").strip():
        prefix = "转发" if kind == "repost" else "链接"
        return f"{prefix}：{str(card['title']).strip()}"
    return f"{_content_kind_label(kind)} - {published_at.date().isoformat()}"


def _looks_like_social_account_title(title: str) -> bool:
    return bool(re.search(r"\(@[A-Za-z0-9_]+\)\s*$", title.strip()))


def _card_media_kind(card: dict[str, object]) -> str:
    media_type = str(card.get("media_type") or "").strip().lower()
    if media_type in {"image", "video"}:
        return media_type
    return "image" if str(card.get("image_url") or "").strip() else ""


def _is_repost_marker_only(content: str) -> bool:
    return bool(re.fullmatch(r"rt[:：]?", content.strip(), re.IGNORECASE))


def _display_content(content: str, kind: str, metadata: dict[str, object], links: list[dict[str, str]]) -> str:
    if content.strip() and not (
        (kind in {"repost", "link"} and _is_link_only(content, links)) or (kind == "repost" and _is_repost_marker_only(content))
    ):
        return content
    card = metadata.get("card")
    if isinstance(card, dict):
        description = str(card.get("description") or "").strip()
        title = str(card.get("title") or "").strip()
        url = str(card.get("url") or "").strip()
        if description:
            return description
        if kind == "repost":
            card_kind = _card_media_kind(card)
            if card_kind == "video":
                return "转发视频内容，点击查看原帖。"
            if card_kind == "image":
                return "转发图片内容，点击查看原帖。"
            if title and not _looks_like_social_account_title(title):
                return title
            return "转发内容，点击查看原帖。"
        if title:
            return title
        if url:
            return f"{_content_kind_label(kind)}：{url}"
    return f"{_content_kind_label(kind)}，点击查看原帖。"


class TrumpsTruthRssAdapter:
    source_name = "trumps_truth_rss"
    label = "TrumpTruth RSS"

    def __init__(self, feed_url: str, timeout_seconds: int, fetcher: Callable[[str, int], str] | None = None) -> None:
        self.feed_url = feed_url
        self.timeout_seconds = timeout_seconds
        self.fetcher = fetcher or (lambda url, timeout: _http_get(url, timeout))

    def poll(self) -> SourcePollResult:
        text = self.fetcher(self.feed_url, self.timeout_seconds)
        root = ET.fromstring(text)
        events: list[SpeechEvent] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or link or title).strip()
            description_html = item.findtext("description") or ""
            description = _html_to_text(description_html)
            links = _extract_links(description_html)
            pub_date_raw = (item.findtext("pubDate") or "").strip()
            if not link or not pub_date_raw:
                continue
            published_at = parsedate_to_datetime(pub_date_raw)
            if published_at.tzinfo is None:
                continue
            payload = ET.tostring(item, encoding="unicode")
            metadata: dict[str, object] = {"feed_url": self.feed_url}
            original_url = _find_child_text(item, "originalUrl")
            original_id = _find_child_text(item, "originalId")
            if original_url:
                metadata["original_url"] = _normalize_truthsocial_post_url(original_url)
            if original_id:
                metadata["original_id"] = original_id
            if links:
                metadata["links"] = links
            if _needs_detail_fetch(title, description, links):
                try:
                    detail = _extract_status_detail_metadata(self.fetcher(link, self.timeout_seconds))
                    metadata.update(detail)
                except Exception as exc:  # noqa: BLE001
                    metadata["detail_fetch_error"] = str(exc)
            if not metadata.get("original_url") and isinstance(metadata.get("original_id"), str):
                metadata["original_url"] = _truthsocial_post_url_from_id(str(metadata["original_id"]))
            content_kind = _classify_content(title, description, links, metadata)
            metadata["content_kind"] = content_kind
            display_title = _display_title(title or "Truth Social post", content_kind, metadata, published_at)
            display_content = _display_content(description, content_kind, metadata, links)
            events.append(
                SpeechEvent(
                    source_type="social",
                    source_name=self.source_name,
                    event_id=guid,
                    published_at=published_at,
                    title=display_title,
                    speaker="Donald Trump",
                    content=display_content,
                    url=link,
                    raw_hash=raw_payload_hash(f"{payload}|{metadata}"),
                    metadata=metadata,
                )
            )
        return SourcePollResult(self.source_name, self.label, events)


class TruthbrushApiAdapter:
    source_name = "truthbrush_api"
    label = "Truthbrush"

    def __init__(self, handle: str) -> None:
        self.handle = handle.lstrip("@")

    def poll(self) -> SourcePollResult:
        try:
            from truthbrush.api import Api
        except ImportError:
            return SourcePollResult(self.source_name, self.label, [], ["truthbrush 未安装，已跳过直接 API 来源。"])

        try:
            api = Api()
            events: list[SpeechEvent] = []
            for post in api.pull_statuses(self.handle, replies=False):
                content = normalize_text(post.get("content", "") or post.get("spoiler_text", "") or "")
                card = post.get("card") or {}
                if not content and card:
                    content = normalize_text(card.get("title", "") or card.get("description", ""))
                title = normalize_text(content.split(".")[0])[:120] or "Truth Social post"
                url = post.get("url") or f"https://truthsocial.com/@{self.handle}/{post.get('id')}"
                events.append(
                    SpeechEvent(
                        source_type="social",
                        source_name=self.source_name,
                        event_id=str(post["id"]),
                        published_at=parse_datetime(post["created_at"]),
                        title=title,
                        speaker="Donald Trump",
                        content=content or title,
                        url=url,
                        raw_hash=raw_payload_hash(str(post)),
                        metadata={"handle": self.handle, "truthbrush": True},
                    )
                )
            return SourcePollResult(self.source_name, self.label, events)
        except Exception as exc:  # noqa: BLE001
            return SourcePollResult(self.source_name, self.label, [], [f"truthbrush 来源失败: {exc}"])


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = normalize_text(unescape("".join(self._text_parts)))
            if self._href and text:
                self.anchors.append((self._href, text))
            self._href = None
            self._text_parts = []


class WhiteHouseGalleryAdapter:
    source_name = "whitehouse_gallery"
    label = "白宫 Gallery"

    def __init__(self, gallery_url: str, timeout_seconds: int, include_keywords: list[str] | None = None, exclude_keywords: list[str] | None = None) -> None:
        self.gallery_url = gallery_url
        self.timeout_seconds = timeout_seconds
        self.include_keywords = [item.casefold() for item in (include_keywords or []) if item.strip()]
        self.exclude_keywords = [item.casefold() for item in (exclude_keywords or []) if item.strip()]

    def poll(self) -> SourcePollResult:
        html = _http_get(self.gallery_url, self.timeout_seconds)
        collector = _AnchorCollector()
        collector.feed(html)
        events: list[SpeechEvent] = []
        seen_urls: set[str] = set()
        for href, text in collector.anchors:
            absolute_url = parse.urljoin(self.gallery_url, href)
            if absolute_url in seen_urls or "/gallery/" not in absolute_url:
                continue
            if not self._matches_keywords(text):
                continue
            seen_urls.add(absolute_url)
            title = re.sub(r"^Image:\s*", "", text).strip()
            events.append(
                SpeechEvent(
                    source_type="public_remarks",
                    source_name=self.source_name,
                    event_id=absolute_url.rstrip("/"),
                    published_at=self._extract_datetime_from_url(absolute_url),
                    title=title,
                    speaker="Donald Trump",
                    content=title,
                    url=absolute_url,
                    raw_hash=raw_payload_hash(f"{absolute_url}|{title}"),
                    metadata={"gallery_url": self.gallery_url},
                )
            )
        return SourcePollResult(self.source_name, self.label, events)

    def _matches_keywords(self, text: str) -> bool:
        normalized = text.casefold()
        if any(keyword in normalized for keyword in self.exclude_keywords):
            return False
        if not self.include_keywords:
            return True
        return any(keyword in normalized for keyword in self.include_keywords)

    @staticmethod
    def _extract_datetime_from_url(url: str) -> datetime:
        match = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])/", url)
        if match:
            year, month = match.groups()
            return datetime(int(year), int(month), 1, tzinfo=timezone.utc)
        return datetime.now(timezone.utc)


class DirectStatementAnalyzer:
    category_rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("market_sentiment", ("tariff", "tariffs", "fed", "rate", "rates", "inflation", "recession", "stock", "stocks", "bitcoin", "btc", "crypto", "ethereum", "eth", "treasury", "dollar", "oil", "gas")),
        ("foreign_policy", ("china", "iran", "russia", "ukraine", "israel", "gaza", "europe", "eu", "mexico", "canada", "nato", "trade deal", "sanction")),
        ("immigration", ("border", "migrant", "migrants", "immigration", "deport", "deportation", "asylum")),
        ("federal_workers", ("federal worker", "federal workers", "agency", "agencies", "bureaucrat", "bureaucrats", "department", "civil service")),
        ("election_integrity", ("election", "ballot", "voter fraud", "rigged", "vote", "voting machine")),
        ("military", ("military", "army", "navy", "air force", "marines", "general", "troops", "strike", "missile")),
    )

    def analyze(self, event: SpeechEvent) -> dict[str, object]:
        text = f"{event.title}\n{event.content}".lower()
        categories: list[str] = []
        for category, keywords in self.category_rules:
            if any(keyword in text for keyword in keywords):
                categories.append(category)
        if not (event.content or "").strip():
            categories.append("no_content")
        if not categories:
            categories.append("personal_or_rhetorical")
        return {"categories": categories, "market_signal": "market_sentiment" in categories}
