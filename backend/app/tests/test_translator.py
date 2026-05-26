from __future__ import annotations

from backend.app.services.translator import Translator, _is_unusable_translation_output, _should_skip_translation


def test_translation_skips_chinese_media_placeholders_and_urls() -> None:
    assert _should_skip_translation("转发图片内容，点击查看原帖。")
    assert _should_skip_translation("https://truthsocial.com/users/example/statuses/1")
    assert _should_skip_translation('<p><a href="https://truthsocial.com/users/example/statuses/1">truthsocial.com/users/example/statuses/1</a></p>')
    assert _should_skip_translation("https:// truthsocial.com/users/Imalucky angel/statuses/116634887599602834")
    assert not _should_skip_translation("The original repost text that should be summarized.")
    assert not _should_skip_translation("转发：These liberal idiots will be proven wrong once again!")


def test_unusable_translation_outputs_are_detected() -> None:
    assert _is_unusable_translation_output("无法处理：需要提供英文新闻标题或摘要文本，而非仅链接。请提供需要翻译的内容。")
    assert _is_unusable_translation_output("无法翻译，因为未提供英文新闻标题或摘要内容。请提供需要翻译的英文文本。")
    assert _is_unusable_translation_output("您提供的链接是一个Truth Social的帖子，但作为AI，我无法访问外部链接。请直接提供需要翻译的英文新闻标题或摘要文本。")
    assert _is_unusable_translation_output("请提供英文新闻标题或摘要，我才能翻译。")
    assert not _is_unusable_translation_output("这是需要显示给用户的正常中文译文。")


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
