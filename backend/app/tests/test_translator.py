from __future__ import annotations

from backend.app.services.translator import _should_skip_translation


def test_translation_skips_chinese_media_placeholders_and_urls() -> None:
    assert _should_skip_translation("转发图片内容，点击查看原帖。")
    assert _should_skip_translation("https://truthsocial.com/users/example/statuses/1")
    assert not _should_skip_translation("The original repost text that should be summarized.")
