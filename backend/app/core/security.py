from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


MASK = "********"


def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_json(payload: dict[str, Any], secret_key: str) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return _fernet(secret_key).encrypt(data).decode("utf-8")


def decrypt_json(ciphertext: str, secret_key: str) -> dict[str, Any]:
    if not ciphertext:
        return {}
    try:
        raw = _fernet(secret_key).decrypt(ciphertext.encode("utf-8"))
    except InvalidToken:
        return {}
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return MASK
    return f"{value[:4]}...{value[-4:]}"


def make_admin_token(secret_key: str, admin_password: str) -> str:
    digest = hmac.new(
        secret_key.encode("utf-8"),
        f"admin:{admin_password}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def verify_admin_token(token: str | None, secret_key: str, admin_password: str) -> bool:
    if not token:
        return False
    expected = make_admin_token(secret_key, admin_password)
    return hmac.compare_digest(token, expected)
