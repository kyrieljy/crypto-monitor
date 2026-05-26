from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .api.schemas import (
    AlertEventOut,
    DashboardLayout,
    DashboardModule,
    KlineOut,
    LoginRequest,
    LoginResponse,
    NewsEventOut,
    NewsTranslateRequest,
    NewsTranslateResponse,
    NotifierTarget,
    NotifierTestResponse,
    NotifierUpsert,
    SnapshotOut,
    SourceHealthOut,
    StrategyConfig,
    StrategyUpdate,
    SymbolItem,
    WhaleAddressResolveRequest,
    WhaleAddressResolveResponse,
    WhaleDetailOut,
    WhaleTargetOut,
    WhaleTargetUpsert,
)
from .core.database import Database
from .core.security import make_admin_token, verify_admin_token
from .core.settings import load_runtime_settings
from .services.events import EventBus
from .services.cleanup_worker import CleanupWorker
from .services.market_data import DataSourceError, DataSourceRouter
from .services.news_runner import NewsRunner
from .services.notification_worker import NotificationWorker
from .services.notifiers import NotificationService
from .services.store import Store
from .services.strategies import TechnicalStrategyRunner
from .services.translator import Translator
from .services.whale_runner import WhaleRunner


settings = load_runtime_settings()
logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")

db = Database(settings.database_path, settings.app_secret_key)
store = Store(db)
bus = EventBus()
market_router = DataSourceRouter(timeout_seconds=settings.request_timeout_seconds)
notification_service = NotificationService(store, timeout_seconds=settings.request_timeout_seconds)
translator = Translator(store, timeout_seconds=settings.request_timeout_seconds)

app = FastAPI(title="Market Monitor Dashboard", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    if not verify_admin_token(token, settings.app_secret_key, settings.admin_password):
        raise HTTPException(status_code=401, detail="需要管理员登录")


@app.on_event("startup")
async def on_startup() -> None:
    bus.bind_loop()
    if settings.run_workers:
        app.state.strategy_task = asyncio.create_task(TechnicalStrategyRunner(store, market_router, bus).run_forever())
        app.state.news_task = asyncio.create_task(NewsRunner(store, translator, bus, settings.request_timeout_seconds).run_forever())
        app.state.notification_task = asyncio.create_task(NotificationWorker(store, notification_service).run_forever())
        app.state.cleanup_task = asyncio.create_task(CleanupWorker(store).run_forever())
        app.state.whale_task = asyncio.create_task(WhaleRunner(store, bus, settings.request_timeout_seconds).run_forever())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    for name in ("strategy_task", "news_task", "notification_task", "cleanup_task", "whale_task"):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()
    db.close()


@app.post("/api/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    if payload.password != settings.admin_password:
        raise HTTPException(status_code=401, detail="密码错误")
    return LoginResponse(token=make_admin_token(settings.app_secret_key, settings.admin_password))


@app.get("/api/settings/symbols", response_model=list[SymbolItem])
def get_symbols() -> list[SymbolItem]:
    return store.list_symbols()


@app.put("/api/settings/symbols", response_model=list[SymbolItem])
def put_symbols(items: list[SymbolItem], _: None = Depends(require_admin)) -> list[SymbolItem]:
    return store.replace_symbols(items)


@app.get("/api/strategies/{strategy_id}", response_model=StrategyConfig)
def get_strategy(strategy_id: str) -> StrategyConfig:
    strategy = store.get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    return strategy


@app.put("/api/strategies/{strategy_id}", response_model=StrategyConfig)
def put_strategy(strategy_id: str, payload: StrategyUpdate, _: None = Depends(require_admin)) -> StrategyConfig:
    try:
        return store.update_strategy(strategy_id, payload.enabled, payload.config, payload.notifier_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="策略不存在") from None


@app.get("/api/notifiers", response_model=list[NotifierTarget])
def get_notifiers() -> list[NotifierTarget]:
    return store.list_notifiers(reveal=False)


@app.put("/api/notifiers", response_model=list[NotifierTarget])
def put_notifiers(items: list[NotifierUpsert], _: None = Depends(require_admin)) -> list[NotifierTarget]:
    incoming_ids = {item.id for item in items}
    for item in items:
        store.upsert_notifier(
            NotifierTarget(
                id=item.id,
                name=item.name,
                type=item.type,
                enabled=item.enabled,
                secrets=item.secrets,
                created_at="",
                updated_at="",
            )
        )
    store.delete_notifiers_not_in(incoming_ids)
    return store.list_notifiers(reveal=False)


@app.post("/api/notifiers/{notifier_id}/test", response_model=NotifierTestResponse)
def test_notifier(notifier_id: str, _: None = Depends(require_admin)) -> NotifierTestResponse:
    try:
        ok, dry_run, message = notification_service.test_notifier(notifier_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="机器人不存在") from None
    return NotifierTestResponse(ok=ok, dry_run=dry_run, message=message)


@app.get("/api/dashboard/modules", response_model=list[DashboardModule])
def get_modules() -> list[DashboardModule]:
    return store.list_modules()


@app.put("/api/dashboard/modules", response_model=list[DashboardModule])
def put_modules(modules: list[DashboardModule], _: None = Depends(require_admin)) -> list[DashboardModule]:
    return store.replace_modules(modules)


@app.get("/api/dashboard/layout", response_model=DashboardLayout)
def get_layout() -> DashboardLayout:
    return store.get_layout()


@app.put("/api/dashboard/layout", response_model=DashboardLayout)
def put_layout(layout: DashboardLayout, _: None = Depends(require_admin)) -> DashboardLayout:
    return store.save_layout(layout)


@app.get("/api/events/alerts", response_model=list[AlertEventOut])
def get_alerts(limit: int = 80) -> list[AlertEventOut]:
    return store.list_alerts(limit)


@app.get("/api/events/news", response_model=list[NewsEventOut])
def get_news(limit: int = 80) -> list[NewsEventOut]:
    return store.list_news(limit)


@app.post("/api/events/news/translate", response_model=NewsTranslateResponse)
def translate_news(payload: NewsTranslateRequest, _: None = Depends(require_admin)) -> NewsTranslateResponse:
    ids = list(dict.fromkeys(payload.ids))
    rows = store.list_news_rows_by_ids(ids)
    updated = 0
    unchanged = 0
    for row in rows:
        translated_title = translator.translate(row["title"])
        translated_summary = translator.translate_summary(row["content"] or row["title"])
        if translated_title != row["translated_title"] or translated_summary != row["translated_summary"]:
            store.update_news_translation(int(row["id"]), translated_title, translated_summary)
            updated += 1
        else:
            unchanged += 1
    return NewsTranslateResponse(requested=len(ids), found=len(rows), updated=updated, unchanged=unchanged)


@app.get("/api/health/sources", response_model=list[SourceHealthOut])
def get_health() -> list[SourceHealthOut]:
    return store.list_health()


@app.get("/api/whales", response_model=list[WhaleTargetOut])
def get_whales() -> list[WhaleTargetOut]:
    return store.list_whale_targets()


@app.post("/api/whales/resolve", response_model=WhaleAddressResolveResponse)
def resolve_whale_address(payload: WhaleAddressResolveRequest, _: None = Depends(require_admin)) -> WhaleAddressResolveResponse:
    return store.resolve_whale_addresses(payload.query)


@app.put("/api/whales", response_model=WhaleTargetOut)
def put_whale_target(payload: WhaleTargetUpsert, _: None = Depends(require_admin)) -> WhaleTargetOut:
    return store.upsert_whale_target(payload)


@app.delete("/api/whales/{target_id}")
def delete_whale_target(target_id: str, _: None = Depends(require_admin)) -> dict[str, bool]:
    store.delete_whale_target(target_id)
    return {"ok": True}


@app.get("/api/whales/{target_id}", response_model=WhaleDetailOut)
def get_whale_detail(target_id: str) -> WhaleDetailOut:
    detail = store.get_whale_detail(target_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="关注地址不存在")
    return detail


@app.get("/api/market/klines/{symbol}/{interval}", response_model=list[KlineOut])
def get_klines(symbol: str, interval: str, limit: int = 80) -> list[KlineOut]:
    if not settings.run_workers:
        return []
    try:
        chart_module = next((module for module in store.list_modules() if module.id == "charts"), None)
        data_source = str((chart_module.config if chart_module else {}).get("data_source", "okx_then_binance"))
        candles, source, source_role = market_router.fetch_klines(symbol.upper(), interval, min(max(limit, 20), 1000), data_source)
    except DataSourceError as exc:
        store.record_source_error("market_data", "行情数据", str(exc))
        return []
    return [
        KlineOut(
            time=int(candle.open_time_ms / 1000),
            open=candle.open_price,
            high=candle.high_price,
            low=candle.low_price,
            close=candle.close_price,
            volume=candle.volume,
            source=source,
            source_role=source_role,
        )
        for candle in candles
    ]


@app.get("/api/snapshot", response_model=SnapshotOut)
def get_snapshot() -> SnapshotOut:
    return SnapshotOut(
        symbols=store.list_symbols(),
        strategies=store.list_strategies(),
        modules=store.list_modules(),
        layout=store.get_layout(),
        alerts=store.list_alerts(500),
        news=store.list_news(50),
        health=store.list_health(),
    )


@app.get("/api/stream")
async def stream():
    async def generator():
        async for queue in bus.subscribe():
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=20)
                    yield message
                except asyncio.TimeoutError:
                    yield f"event: heartbeat\ndata: {json.dumps({'ok': True})}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")


def _frontend_file_response(path: Path) -> FileResponse:
    response = FileResponse(path)
    if path.name == "index.html":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/{path:path}")
def serve_frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="接口不存在")
    index = frontend_dist / "index.html"
    target = frontend_dist / path
    if target.exists() and target.is_file():
        return _frontend_file_response(target)
    if index.exists():
        return _frontend_file_response(index)
    return {"message": "前端尚未构建，请在 frontend 目录运行 npm run build。"}
