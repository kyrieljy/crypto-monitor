from __future__ import annotations

import json
import socket
from typing import Any
from urllib import error, request

from ..core.security import decrypt_json
from .store import Store


class NotifierError(Exception):
    pass


def _post_json(url: str, payload: dict[str, Any], timeout_seconds: int = 20) -> bool:
    req = request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_seconds) as resp:
        return 200 <= resp.status < 300


def _send_error_message(channel: str, exc: Exception) -> str:
    if isinstance(exc, error.HTTPError):
        return f"{channel}发送失败：Webhook 返回 HTTP {exc.code}，请检查机器人地址是否正确。"
    if isinstance(exc, error.URLError):
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return f"{channel}发送失败：连接超时，请检查网络或机器人 webhook 是否可访问。"
        return f"{channel}发送失败：无法连接 webhook，原因：{reason}"
    if isinstance(exc, TimeoutError | socket.timeout):
        return f"{channel}发送失败：连接超时，请稍后重试。"
    return f"{channel}发送失败：{exc}"


class NotificationService:
    def __init__(self, store: Store, timeout_seconds: int = 20) -> None:
        self.store = store
        self.timeout_seconds = timeout_seconds

    def test_notifier(self, notifier_id: str) -> tuple[bool, bool, str]:
        notifier = self.store.get_notifier(notifier_id, reveal=True)
        if notifier is None:
            raise KeyError(notifier_id)
        message = "【测试通知】Market Monitor 机器人配置可用。"
        return self._send_to_notifier(notifier.id, message, reveal=True)

    def send_strategy_message(self, strategy_id: str, message: str) -> tuple[bool, bool, str]:
        strategy = self.store.get_strategy(strategy_id)
        if strategy is None or not strategy.notifier_id:
            return True, True, "策略未绑定机器人，跳过发送"
        return self._send_to_notifier(strategy.notifier_id, message, reveal=True)

    def _send_to_notifier(self, notifier_id: str, message: str, *, reveal: bool = True) -> tuple[bool, bool, str]:
        row = self.store.db.query_one("SELECT * FROM notifier_targets WHERE id = ?", (notifier_id,))
        if row is None:
            raise KeyError(notifier_id)
        if not bool(row["enabled"]):
            return True, True, "机器人未启用，按 dry-run 处理"
        secrets = decrypt_json(row["secret_json"], self.store.db.secret_key) if reveal else {}
        if row["type"] == "feishu":
            webhook_url = str(secrets.get("webhook_url") or "")
            if not webhook_url:
                return True, True, "飞书 webhook 未配置，按 dry-run 处理"
            try:
                ok = _post_json(webhook_url, {"msg_type": "text", "content": {"text": message}}, self.timeout_seconds)
            except Exception as exc:
                return False, False, _send_error_message("飞书", exc)
            return ok, False, "飞书发送成功" if ok else "飞书返回非成功状态"
        if row["type"] == "telegram":
            token = str(secrets.get("bot_token") or "")
            chat_id = str(secrets.get("chat_id") or "")
            if not token or not chat_id:
                return True, True, "Telegram token/chat_id 未配置，按 dry-run 处理"
            try:
                ok = _post_json(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    {"chat_id": chat_id, "text": message},
                    self.timeout_seconds,
                )
            except Exception as exc:
                return False, False, _send_error_message("Telegram", exc)
            return ok, False, "Telegram 发送成功" if ok else "Telegram 返回非成功状态"
        raise NotifierError(f"Unsupported notifier type: {row['type']}")
