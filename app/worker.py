from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import get_engine
from app.services.alert_service import (
    active_tickers,
    list_active_alerts,
    log_event,
    mark_checked,
    mark_triggered,
)
from app.services.depth_checker import evaluate_alert
from app.services.ibkr_client import IbkrDepthClient
from app.services.push_notifier import send_alert_notification

logger = logging.getLogger(__name__)

WorkerState = Literal["idle", "connecting", "connected", "backoff"]


def _session() -> Session:
    SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return SessionLocal()


@dataclass(frozen=True)
class WorkerStatus:
    worker_state: WorkerState
    worker_connected: bool
    worker_last_error: str | None
    depth_subscriptions: int


class DepthWorker:
    def __init__(self) -> None:
        self.client = IbkrDepthClient()
        self._handlers: dict[str, object] = {}
        self._rotation_index = 0
        self._running = False
        self.worker_state: WorkerState = "idle"
        self.worker_last_error: str | None = None
        self._reconnect_event = asyncio.Event()

    @property
    def depth_subscription_count(self) -> int:
        return len(self._handlers)

    def request_reconnect(self) -> None:
        if self.client.connected:
            return
        self._reconnect_event.set()

    async def _interruptible_sleep(self, seconds: float) -> None:
        sleep_task = asyncio.create_task(asyncio.sleep(seconds))
        reconnect_task = asyncio.create_task(self._reconnect_event.wait())
        done, pending = await asyncio.wait(
            {sleep_task, reconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._reconnect_event.clear()

    async def run(self) -> None:
        self._running = True
        backoff = 5
        while self._running:
            try:
                self.worker_state = "connecting"
                self.worker_last_error = None
                await self.client.connect()
                self.worker_state = "connected"
                backoff = 5
                await self._run_connected_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Depth worker error: %s", exc)
                self.worker_last_error = str(exc)[:500]
                self.client.disconnect()
                self.worker_state = "backoff"
                await self._interruptible_sleep(backoff)
                backoff = min(backoff * 2, 120)
            else:
                if not self.client.connected:
                    self.worker_state = "idle"

    async def _run_connected_loop(self) -> None:
        while self._running and self.client.connected:
            try:
                await self._sync_subscriptions()
            except Exception as exc:
                logger.exception("Depth worker subscription error: %s", exc)
                self.worker_last_error = str(exc)[:500]
                raise
            await asyncio.sleep(30)

    async def _sync_subscriptions(self) -> None:
        settings = get_settings()
        session = _session()
        try:
            alerts = list_active_alerts(session)
            tickers = active_tickers(alerts)
            if len(tickers) > settings.ibkr.max_depth_symbols:
                tickers = self._rotate_tickers(tickers, settings.ibkr.max_depth_symbols)

            desired_set = set(tickers)
            for ticker in list(self._handlers.keys()):
                if ticker in desired_set:
                    continue
                market_ticker = self.client.get_ticker(ticker)
                if market_ticker is not None:
                    market_ticker.updateEvent -= self._handlers[ticker]
                del self._handlers[ticker]

            market_tickers = self.client.sync_subscriptions(tickers)
            for ticker, market_ticker in market_tickers.items():
                if ticker in self._handlers:
                    continue

                def make_handler(symbol: str):
                    def on_update(ticker_obj) -> None:
                        self._handle_depth_update(symbol, ticker_obj)

                    return on_update

                handler = make_handler(ticker)
                market_ticker.updateEvent += handler
                self._handlers[ticker] = handler
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _rotate_tickers(self, tickers: list[str], limit: int) -> list[str]:
        if not tickers:
            return []
        if len(tickers) <= limit:
            return tickers
        start = self._rotation_index % len(tickers)
        rotated = tickers[start:] + tickers[:start]
        self._rotation_index = (self._rotation_index + limit) % len(tickers)
        return rotated[:limit]

    def _handle_depth_update(self, ticker: str, ticker_obj) -> None:
        session = _session()
        try:
            alerts = [alert for alert in list_active_alerts(session) if alert.ticker == ticker]
            if not alerts:
                return

            dom_bids = list(ticker_obj.domBids)
            dom_asks = list(ticker_obj.domAsks)
            depth_snapshot = {
                "bids": [{"price": level.price, "size": level.size} for level in dom_bids],
                "asks": [{"price": level.price, "size": level.size} for level in dom_asks],
                "checked_at": datetime.utcnow().isoformat(),
            }
            depth_json = json.dumps(depth_snapshot)

            for alert in alerts:
                evaluation = evaluate_alert(alert, dom_bids, dom_asks)
                if evaluation.triggered:
                    mark_triggered(session, alert, depth_json)
                    session.commit()
                    send_alert_notification(alert, evaluation.available)
                    log_event(
                        session,
                        alert.id,
                        "trigger",
                        f"available={evaluation.available}",
                    )
                    session.commit()
                else:
                    mark_checked(session, alert, depth_json)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception("Failed processing depth update for %s: %s", ticker, exc)
        finally:
            session.close()

    def stop(self) -> None:
        self._running = False
        self.worker_state = "idle"
        self.client.disconnect()


_worker: DepthWorker | None = None


def get_worker() -> DepthWorker | None:
    return _worker


def request_worker_reconnect() -> None:
    worker = get_worker()
    if worker is not None:
        worker.request_reconnect()


def get_worker_status() -> WorkerStatus:
    worker = get_worker()
    if worker is None:
        return WorkerStatus(
            worker_state="idle",
            worker_connected=False,
            worker_last_error=None,
            depth_subscriptions=0,
        )
    return WorkerStatus(
        worker_state=worker.worker_state,
        worker_connected=worker.client.connected,
        worker_last_error=worker.worker_last_error,
        depth_subscriptions=worker.depth_subscription_count,
    )


def disconnect_ibkr_client() -> None:
    worker = get_worker()
    if worker is not None:
        worker.client.disconnect()


def is_ibkr_connected() -> bool:
    try:
        return _worker is not None and _worker.client.connected
    except Exception:
        logger.exception("Failed checking IBKR worker connection state")
        return False


async def run_depth_worker() -> None:
    from ib_insync import util

    util.patchAsyncio()
    global _worker
    _worker = DepthWorker()
    await _worker.run()
