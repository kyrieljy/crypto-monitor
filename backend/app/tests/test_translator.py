from __future__ import annotations

from backend.app.services.translator import Translator, _should_skip_translation


def test_translation_skips_chinese_media_placeholders_and_urls() -> None:
    assert _should_skip_translation("转发图片内容，点击查看原帖。")
    assert _should_skip_translation("https://truthsocial.com/users/example/statuses/1")
    assert not _should_skip_translation("The original repost text that should be summarized.")
    assert not _should_skip_translation("转发：These liberal idiots will be proven wrong once again!")


class FakeTranslator(Translator):
    def __init__(self) -> None:
        pass

    def translate(self, text: str) -> str:
        return f"译文：{text}"

    def translate_summary(self, text: str) -> str:
        return f"摘要：{text}"


def test_card_description_translation_reuses_event_summary() -> None:
    metadata = {
        "content_kind": "repost",
        "card": {
            "title": "WomenForTrump (@IStandWithTrump47)",
            "description": "Without this treasonous traitor we wouldn’t be in this situation at all!",
            "url": "https://truthsocial.com/users/IStandWithTrump47/statuses/1",
        },
    }

    translated = FakeTranslator().translate_metadata(
        metadata,
        source_content="Without this treasonous traitor we wouldn’t be in this situation at all!",
        translated_summary="没有这个叛国者，我们根本不会陷入这种境地！",
    )

    assert translated["card"]["translated_description"] == "没有这个叛国者，我们根本不会陷入这种境地！"
    assert "translated_title" not in translated["card"]
