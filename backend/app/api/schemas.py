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
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class NotifierUpsert(BaseModel):
    id: str
    name: str
    type: Literal["feishu", "telegram"]
    enabled: bool
    secrets: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


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


class BtcLargeTransferOut(BaseModel):
    txid: str
    chain: str = "btc"
    asset: str = "BTC"
    block_height: int
    block_hash: str
    block_time_utc: str
    amount: float = 0
    amount_btc: float
    total_input_amount: float = 0
    total_output_amount: float = 0
    fee_amount: float = 0
    total_input_btc: float
    total_output_btc: float
    fee_btc: float
    input_addresses: list[dict[str, Any]] = Field(default_factory=list)
    output_addresses: list[dict[str, Any]] = Field(default_factory=list)
    address_operations: list[dict[str, Any]] = Field(default_factory=list)
    exchange_hints: list[str] = Field(default_factory=list)
    source_url: str
    raw: dict[str, Any] = Field(default_factory=dict)
    match_count: int = 0
    matches: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str


class BtcLargeTransferListOut(BaseModel):
    items: list[BtcLargeTransferOut]
    total: int
    limit: int
    offset: int


class BtcLargeTransferStatsOut(BaseModel):
    total: int
    today_count: int
    latest_block_height: int | None = None
    latest_eth_block_height: int | None = None
    latest_scanned_height: int | None = None
    latest_eth_scanned_height: int | None = None
    latest_scan_time: str | None = None
    latest_eth_scan_time: str | None = None
    min_btc: float
    min_eth: float = 0
    matched_count: int


class BtcLargeTransferRescanRequest(BaseModel):
    blocks: int | None = Field(default=3, ge=1, le=50)
    start_utc: str | None = None
    end_utc: str | None = None
    max_blocks: int = Field(default=24, ge=1, le=288)


class BtcLargeTransferRescanResponse(BaseModel):
    ok: bool
    scanned_blocks: int = 0
    scanned_eth_blocks: int = 0
    inserted: int = 0
    inserted_eth: int = 0
    latest_height: int | None = None
    latest_eth_height: int | None = None
    start_height: int | None = None
    end_height: int | None = None
    start_eth_height: int | None = None
    end_eth_height: int | None = None
    message: str = ""


class BtcAddressConfirmRequest(BaseModel):
    address: str
    role: Literal["candidate", "confirmed"] = "candidate"
    label: str | None = None


class IbitHistorySyncRequest(BaseModel):
    lookback_days: int = Field(default=30, ge=1, le=90)
    max_news_items: int = Field(default=300, ge=10, le=1000)


class IbitHistorySyncResponse(BaseModel):
    ok: bool
    target_id: str
    lookback_days: int
    address_count: int = 0
    account_operation_count: int = 0
    news_signal_count: int = 0
    matched_address_count: int = 0
    large_transfer_match_count: int = 0
    eth_large_transfer_inserted: int = 0
    message: str = ""


class IbitHistorySyncJobStatus(BaseModel):
    job_id: str
    target_id: str
    status: Literal["pending", "running", "completed", "failed"]
    stage: str = ""
    message: str = ""
    progress: float = 0
    current: int = 0
    total: int = 0
    started_at: str
    updated_at: str
    completed_at: str | None = None
    result: IbitHistorySyncResponse | None = None
