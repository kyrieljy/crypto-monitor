from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_datetime(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    for parser in (
        datetime.fromisoformat,
        lambda v: datetime.strptime(v, "%a, %d %b %Y %H:%M:%S %z"),
        lambda v: datetime.strptime(v, "%Y-%m-%d"),
    ):
        try:
            parsed = parser(text)
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unsupported datetime: {value}")
