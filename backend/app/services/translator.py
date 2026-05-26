from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from html import unescape
from urllib import request

from ..core.security import decrypt_json
from ..core.text import summarize_text
from .store import Store


LOGGER = logging.getLogger("market_monitor.translation")


class Translator:
    def __init__(self, store: Store, timeout_seconds: int = 20) -> None:
        self.store = store
        self.timeout_seconds = timeout_seconds

    def translate(self, text: str) -> str:
        if not text.strip():
            return text
        if _should_skip_translation(text):
            return text
        strategy = self.store.get_strategy("translation")
        if strategy is None or not strategy.enabled or not strategy.config.get("enabled"):
            return text
        api_url = str(strategy.config.get("api_url") or "").rstrip("/")
        model = str(strategy.config.get("model") or "")
        secret = decrypt_json(self.store.state_get("translation_secret") or "", self.store.db.secret_key)
        api_key = str(secret.get("api_key") or "")
        if not api_url or not model or not api_key:
            return text
        try:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "把用户提供的英文新闻标题或摘要翻译成简洁自然的简体中文。保留人名、币种、数字、链接和事实含义。只返回中文译文。",
                    },
                    {"role": "user", "content": text},
                ],
                "temperature": float(strategy.config.get("temperature", 0.2)),
            }
            req = request.Request(
                f"{api_url}/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                response_payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            choices = response_payload.get("choices") or []
            if not choices:
                return text
            translated = str(choices[0]["message"]["content"]).strip()
            if not translated or _is_unusable_translation_output(translated):
                return text
            return translated
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("大模型翻译失败，使用原文。error=%s", exc)
            return text

    def translate_summary(self, text: str) -> str:
        return self.translate(summarize_text(text, 300))

    def translate_metadata(
        self,
        metadata: dict[str, object],
        *,
        source_content: str = "",
        translated_summary: str = "",
    ) -> dict[str, object]:
        result = deepcopy(metadata)
        card = result.get("card")
        if not isinstance(card, dict):
            return result
        description = str(card.get("description") or "").strip()
        if description:
            if translated_summary and _same_plain_text(description, source_content):
                card["translated_description"] = translated_summary
            else:
                card["translated_description"] = self.translate_summary(description)
        title = str(card.get("title") or "").strip()
        if title and not _looks_like_social_account_title(title):
            card["translated_title"] = self.translate(title)
        return result


def _should_skip_translation(text: str) -> bool:
    value = _visible_translation_text(text)
    if not value:
        return True
    if re.fullmatch(r"https?://\S+", value):
        return True
    compact_value = re.sub(r"\s+", "", value)
    if re.fullmatch(r"(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}/\S+", compact_value, flags=re.IGNORECASE):
        return True
    without_links = re.sub(r"https?://\S+", "", value, flags=re.IGNORECASE)
    without_links = re.sub(r"https?:\s*/\s*/\s*", "", without_links, flags=re.IGNORECASE)
    without_links = re.sub(r"\b(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?", "", without_links, flags=re.IGNORECASE)
    if not re.sub(r"[\W_]+", "", without_links):
        return True
    chinese_count = len(re.findall(r"[\u3400-\u9fff]", value))
    latin_count = len(re.findall(r"[A-Za-z]", value))
    return chinese_count >= 2 and chinese_count >= latin_count


def _is_unusable_translation_output(text: str) -> bool:
    value = re.sub(r"\s+", " ", text.strip())
    if not value:
        return True
    patterns = (
        r"无法翻译",
        r"无法处理[：:]",
        r"需要提供.*英文新闻标题或摘要",
        r"未提供.*英文新闻标题或摘要",
        r"未提供.*需要翻译",
        r"未提供.*英文文本",
        r"请.*提供.*英文新闻标题或摘要",
        r"请提供.*英文新闻标题或摘要",
        r"请提供.*需要翻译的内容",
        r"请提供.*需要翻译的英文文本",
        r"请提供.*英文文本",
        r"无法访问外部链接",
        r"链接是.*帖子",
        r"作为\s*AI.*无法访问",
        r"不是.*英文新闻标题或摘要",
        r"重新提供.*英文内容",
        r"英文内容.*以便翻译",
        r"未包含任何需要翻译",
        r"没有.*可翻译的内容",
        r"并非仅链接",
        r"无法查看图片",
        r"当前信息仅包含日期",
        r"目前只输入了",
        r"目前仅看到日期信息",
    )
    return any(re.search(pattern, value) for pattern in patterns)


def _visible_translation_text(text: str) -> str:
    value = re.sub(r"<[^>]+>", " ", text.strip())
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _same_plain_text(left: str, right: str) -> bool:
    return re.sub(r"\s+", " ", left.strip()) == re.sub(r"\s+", " ", right.strip())


def _looks_like_social_account_title(value: str) -> bool:
    return bool(re.search(r"\(@[A-Za-z0-9_]+\)\s*$", value.strip()))
