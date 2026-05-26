from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_text(text: str) -> str:
    text = normalize_whitespace(text)
    text = re.sub(r"https?://\S+", "", text)
    return text.strip()


def summarize_text(text: str, limit: int = 300) -> str:
    cleaned = normalize_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def content_fingerprint(title: str, content: str) -> str:
    normalized = normalize_text(f"{title} {content}").lower()
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = normalize_whitespace(normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def raw_payload_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_probably_same_statement(
    title_a: str,
    content_a: str,
    title_b: str,
    content_b: str,
    *,
    threshold: float = 0.92,
) -> bool:
    left = normalize_text(f"{title_a} {content_a}").lower()
    right = normalize_text(f"{title_b} {content_b}").lower()
    if not left or not right:
        return False
    return SequenceMatcher(None, left, right).ratio() >= threshold
