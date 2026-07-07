from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .api.schemas import (
    AlertEventOut,
    BtcAddressConfirmRequest,
    BtcLargeTransferListOut,
    BtcLargeTransferOut,
    BtcLargeTransferRescanRequest,
    BtcLargeTransferRescanResponse,
    BtcLargeTransferStatsOut,
    IbitHistorySyncRequest,
    IbitHistorySyncJobStatus,
    IbitHistorySyncResponse,
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
LOGGER = logging.getLogger("market_monitor.api")

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

_IBIT_HISTORY_JOBS: dict[str, dict[str, Any]] = {}
_IBIT_HISTORY_JOBS_LOCK = threading.Lock()
_IBIT_HISTORY_JOB_LIMIT = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_progress(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _int_value(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _create_ibit_history_job(target_id: str) -> dict[str, Any]:
    now = _now_iso()
    job = {
        "job_id": uuid.uuid4().hex,
        "target_id": target_id,
        "status": "pending",
        "stage": "排队中",
        "message": "等待开始回扫",
        "progress": 0.0,
        "current": 0,
        "total": 100,
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "result": None,
    }
    with _IBIT_HISTORY_JOBS_LOCK:
        _IBIT_HISTORY_JOBS[job["job_id"]] = job
        _prune_ibit_history_jobs_locked()
    return dict(job)


def _prune_ibit_history_jobs_locked() -> None:
    if len(_IBIT_HISTORY_JOBS) <= _IBIT_HISTORY_JOB_LIMIT:
        return
    terminal_jobs = sorted(
        (
            (job_id, job)
            for job_id, job in _IBIT_HISTORY_JOBS.items()
            if job.get("status") in {"completed", "failed"}
        ),
        key=lambda item: str(item[1].get("updated_at") or ""),
    )
    for job_id, _job in terminal_jobs:
        if len(_IBIT_HISTORY_JOBS) <= _IBIT_HISTORY_JOB_LIMIT:
            break
        _IBIT_HISTORY_JOBS.pop(job_id, None)


def _update_ibit_history_job(job_id: str, **fields: Any) -> None:
    allowed = {"status", "stage", "message", "progress", "current", "total", "completed_at", "result"}
    with _IBIT_HISTORY_JOBS_LOCK:
        job = _IBIT_HISTORY_JOBS.get(job_id)
        if job is None:
            return
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "progress":
                value = _clamp_progress(value)
            elif key in {"current", "total"}:
                value = _int_value(value)
            job[key] = value
        job["updated_at"] = _now_iso()


def _get_ibit_history_job(job_id: str) -> dict[str, Any]:
    with _IBIT_HISTORY_JOBS_LOCK:
        job = _IBIT_HISTORY_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="IBIT 回扫任务不存在")
        return dict(job)


def _run_ibit_history_job(job_id: str, target_id: str, lookback_days: int, max_news_items: int) -> None:
    def progress_callback(update: dict[str, Any]) -> None:
        if not isinstance(update, dict):
            return
        payload: dict[str, Any] = {"status": "running"}
        for key in ("stage", "message", "progress", "current", "total"):
            if key in update:
                payload[key] = update[key]
        _update_ibit_history_job(job_id, **payload)
        LOGGER.info(
            "IBIT history sync progress job=%s target=%s stage=%s progress=%.0f current=%s total=%s message=%s",
            job_id,
            target_id,
            str(payload.get("stage") or ""),
            _clamp_progress(payload.get("progress")),
            payload.get("current", ""),
            payload.get("total", ""),
            str(payload.get("message") or "")[:200],
        )

    _update_ibit_history_job(job_id, status="running", stage="准备回扫", message="正在初始化 IBIT 历史回扫", progress=1, current=0, total=100)
    try:
        result = WhaleRunner(store, bus, settings.request_timeout_seconds).sync_ibit_history_now(
            target_id,
            lookback_days=lookback_days,
            max_news_items=max_news_items,
            progress_callback=progress_callback,
        )
    except KeyError:
        message = "关注对象不存在"
        _update_ibit_history_job(job_id, status="failed", stage="失败", message=message, progress=100, completed_at=_now_iso())
    except ValueError as exc:
        _update_ibit_history_job(job_id, status="failed", stage="失败", message=str(exc), progress=100, completed_at=_now_iso())
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("IBIT历史回扫后台任务失败 target=%s job=%s", target_id, job_id)
        message = str(exc)[:500]
        result = {"ok": False, "target_id": target_id, "lookback_days": lookback_days, "message": message}
        _update_ibit_history_job(job_id, status="failed", stage="失败", message=message, progress=100, completed_at=_now_iso(), result=result)
    else:
        message = str(result.get("message") or "回扫完成")
        _update_ibit_history_job(
            job_id,
            status="completed",
            stage="完成",
            message=message,
            progress=100,
            current=100,
            total=100,
            completed_at=_now_iso(),
            result=result,
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
                config=item.config,
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
        metadata = json.loads(row["metadata_json"] or "{}")
        translated_metadata = translator.translate_metadata(
            metadata,
            source_content=row["content"] or row["title"],
            translated_summary=translated_summary,
        )
        if (
            translated_title != row["translated_title"]
            or translated_summary != row["translated_summary"]
            or translated_metadata != metadata
        ):
            store.update_news_translation(int(row["id"]), translated_title, translated_summary, translated_metadata)
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
def put_whale_target(payload: WhaleTargetUpsert, background_tasks: BackgroundTasks, _: None = Depends(require_admin)) -> WhaleTargetOut:
    target = store.upsert_whale_target(payload)
    if target.enabled:
        background_tasks.add_task(_sync_whale_target_after_upsert, target.id)
    return target


def _sync_whale_target_after_upsert(target_id: str) -> None:
    try:
        WhaleRunner(store, bus, settings.request_timeout_seconds).sync_target_now(target_id, force_extended=True)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("新增/更新巨鲸对象后立即同步失败 target=%s error=%s", target_id, exc)


@app.delete("/api/whales/{target_id}")
def delete_whale_target(target_id: str, _: None = Depends(require_admin)) -> dict[str, bool]:
    store.delete_whale_target(target_id)
    return {"ok": True}


@app.post("/api/whales/{target_id}/btc-addresses/confirm", response_model=WhaleTargetOut)
def confirm_whale_btc_address(target_id: str, payload: BtcAddressConfirmRequest, _: None = Depends(require_admin)) -> WhaleTargetOut:
    try:
        return store.confirm_btc_address_for_target(target_id, payload.address, role=payload.role, label=payload.label)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="关注对象不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/whales/{target_id}/ibit-history-sync", response_model=IbitHistorySyncResponse)
def sync_ibit_history(target_id: str, payload: IbitHistorySyncRequest, _: None = Depends(require_admin)) -> IbitHistorySyncResponse:
    try:
        result = WhaleRunner(store, bus, settings.request_timeout_seconds).sync_ibit_history_now(
            target_id,
            lookback_days=payload.lookback_days,
            max_news_items=payload.max_news_items,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="关注对象不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("IBIT历史回扫失败 target=%s", target_id)
        return IbitHistorySyncResponse(ok=False, target_id=target_id, lookback_days=payload.lookback_days, message=str(exc)[:500])
    return IbitHistorySyncResponse(**result)


@app.post("/api/whales/{target_id}/ibit-history-sync/jobs", response_model=IbitHistorySyncJobStatus)
def start_ibit_history_job(target_id: str, payload: IbitHistorySyncRequest, _: None = Depends(require_admin)) -> IbitHistorySyncJobStatus:
    if store.get_whale_target(target_id) is None:
        raise HTTPException(status_code=404, detail="关注对象不存在")
    job = _create_ibit_history_job(target_id)
    thread = threading.Thread(
        target=_run_ibit_history_job,
        args=(job["job_id"], target_id, payload.lookback_days, payload.max_news_items),
        daemon=True,
        name=f"ibit-history-{str(target_id)[:8]}",
    )
    thread.start()
    return IbitHistorySyncJobStatus(**job)


@app.get("/api/whales/{target_id}/ibit-history-sync/jobs/{job_id}", response_model=IbitHistorySyncJobStatus)
def get_ibit_history_job(target_id: str, job_id: str, _: None = Depends(require_admin)) -> IbitHistorySyncJobStatus:
    job = _get_ibit_history_job(job_id)
    if job.get("target_id") != target_id:
        raise HTTPException(status_code=404, detail="IBIT 回扫任务不存在")
    return IbitHistorySyncJobStatus(**job)


@app.get("/api/whales/{target_id}", response_model=WhaleDetailOut)
def get_whale_detail(target_id: str) -> WhaleDetailOut:
    detail = store.get_whale_detail(target_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="关注地址不存在")
    return detail


@app.get("/api/btc/large-transfers", response_model=BtcLargeTransferListOut)
def get_btc_large_transfers(
    limit: int = 50,
    offset: int = 0,
    min_btc: float | None = None,
    query: str = "",
    matched_only: bool = False,
) -> BtcLargeTransferListOut:
    return store.list_btc_large_transfers(limit=limit, offset=offset, min_btc=min_btc, query=query, matched_only=matched_only)


@app.get("/api/btc/large-transfers/stats", response_model=BtcLargeTransferStatsOut)
def get_btc_large_transfer_stats() -> BtcLargeTransferStatsOut:
    strategy = store.get_strategy("whale")
    config = strategy.config if strategy else {}
    min_btc = float(config.get("btc_candidate_min_btc") or 500)
    min_eth = float(config.get("eth_candidate_min_eth") or 5000)
    return store.btc_large_transfer_stats(min_btc=min_btc, min_eth=min_eth)


@app.get("/api/btc/large-transfers/{txid}", response_model=BtcLargeTransferOut)
def get_btc_large_transfer(txid: str) -> BtcLargeTransferOut:
    transfer = store.get_btc_large_transfer(txid)
    if transfer is None:
        raise HTTPException(status_code=404, detail="BTC 大额交易不存在")
    return transfer


@app.post("/api/btc/large-transfers/rescan", response_model=BtcLargeTransferRescanResponse)
def rescan_btc_large_transfers(payload: BtcLargeTransferRescanRequest, _: None = Depends(require_admin)) -> BtcLargeTransferRescanResponse:
    try:
        runner = WhaleRunner(store, bus, settings.request_timeout_seconds)
        result = runner.sync_btc_large_transfers_now(
            blocks=payload.blocks,
            start_utc=payload.start_utc,
            end_utc=payload.end_utc,
            max_blocks=payload.max_blocks,
        )
        strategy = store.get_strategy("whale")
        config = strategy.config if strategy else {}
        eth_result: dict[str, Any] = {}
        eth_message = ""
        if bool(config.get("eth_candidate_monitor_enabled", True)) and bool(config.get("etherscan_enabled", False)):
            try:
                eth_result = runner.sync_eth_large_transfers_now(
                    blocks=payload.blocks,
                    start_utc=payload.start_utc,
                    end_utc=payload.end_utc,
                    max_blocks=payload.max_blocks,
                )
            except Exception as exc:  # noqa: BLE001
                eth_message = f"ETH scan skipped: {str(exc)[:300]}"
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("BTC 大额底表手动补扫失败")
        return BtcLargeTransferRescanResponse(ok=False, message=str(exc)[:500])
    return BtcLargeTransferRescanResponse(
        ok=bool(result.get("ok")),
        scanned_blocks=int(result.get("scanned_blocks") or 0),
        scanned_eth_blocks=int(eth_result.get("scanned_blocks") or 0),
        inserted=int(result.get("inserted") or 0),
        inserted_eth=int(eth_result.get("inserted") or 0),
        latest_height=result.get("latest_height"),
        latest_eth_height=eth_result.get("latest_height"),
        start_height=result.get("start_height"),
        end_height=result.get("end_height"),
        start_eth_height=eth_result.get("start_height"),
        end_eth_height=eth_result.get("end_height"),
        message="; ".join(item for item in (str(result.get("message") or ""), eth_message, str(eth_result.get("message") or "")) if item),
    )


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
