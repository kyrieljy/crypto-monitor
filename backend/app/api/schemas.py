from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str


class SymbolItem(BaseModel):
    symbol: str
    display_name: str
    enabled: bool = True
    sort_order: int = 0


class StrategyConfig(BaseModel):
    id: str
    name: str
    type: str
    enabled: bool
    config: dict[str, Any]
    notifier_id: str | None = None
    updated_at: str


class StrategyUpdate(BaseModel):
    enabled: bool
    config: dict[str, Any]
    notifier_id: str | None = None


class NotifierTarget(BaseModel):
    id: str
    name: str
    type: Literal["feishu", "telegram"]
    enabled: bool
    secrets: dict[str, str] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class NotifierUpsert(BaseModel):
    id: str
    name: str
    type: Literal["feishu", "telegram"]
    enabled: bool
    secrets: dict[str, str] = Field(default_factory=dict)


class NotifierTestResponse(BaseModel):
    ok: bool
    dry_run: bool = False
    message: str


class DashboardModule(BaseModel):
    id: str
    title: str
    enabled: bool
    visible: bool
    config: dict[str, Any]


class DashboardLayout(BaseModel):
    id: str = "default"
    theme: Literal["dark", "light"] = "dark"
    layout: list[dict[str, Any]]
    updated_at: str | None = None


class AlertEventOut(BaseModel):
    id: int
    strategy_id: str
    symbol: str
    interval: str
    signal: str
    severity: str
    message: str
    detail: dict[str, Any]
    candle_open_time_ms: int | None
    close_price: float | None
    source: str
    source_role: str
    created_at: str


class NewsEventOut(BaseModel):
    id: int
    source_type: str
    source_name: str
    published_at_utc: str
    title: str
    translated_title: str
    speaker: str
    content: str
    translated_summary: str
    url: str
    metadata: dict[str, Any]
    first_seen_utc: str
    notification_sent: bool


class NewsTranslateRequest(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=20)


class NewsTranslateResponse(BaseModel):
    requested: int
    found: int
    updated: int
    unchanged: int


class SourceHealthOut(BaseModel):
    source_name: str
    label: str
    status: str
    last_success_utc: str | None = None
    last_error_utc: str | None = None
    last_error_message: str | None = None


class KlineOut(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str
    source_role: str


class SnapshotOut(BaseModel):
    symbols: list[SymbolItem]
    strategies: list[StrategyConfig]
    modules: list[DashboardModule]
    layout: DashboardLayout
    alerts: list[AlertEventOut]
    news: list[NewsEventOut]
    health: list[SourceHealthOut]


class WhaleTargetOut(BaseModel):
    id: str
    label: str
    address_or_subject: str
    enabled: bool
    config: dict[str, Any]
    updated_at: str


class WhaleTargetUpsert(BaseModel):
    id: str | None = None
    label: str
    address_or_subject: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class WhaleAddressResolveRequest(BaseModel):
    query: str


class WhaleAddressCandidate(BaseModel):
    address: str
    label: str
    source: str
    chain: str = "evm"
    url: str | None = None
    confidence: float = 0.8
    target_id: str | None = None


class WhaleAddressResolveResponse(BaseModel):
    query: str
    candidates: list[WhaleAddressCandidate]


class WhaleDetailOut(BaseModel):
    target: WhaleTargetOut
    recent_events: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    holdings: list[dict[str, Any]]
    defi_positions: list[dict[str, Any]] = Field(default_factory=list)
    open_orders: list[dict[str, Any]] = Field(default_factory=list)
    fills: list[dict[str, Any]] = Field(default_factory=list)
    historical_orders: list[dict[str, Any]] = Field(default_factory=list)
    funding: list[dict[str, Any]] = Field(default_factory=list)
    ledger_updates: list[dict[str, Any]] = Field(default_factory=list)
    portfolio: list[dict[str, Any]] = Field(default_factory=list)
    account_summary: dict[str, Any] = Field(default_factory=dict)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    updated_at: str | None = None
