from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


def _ib_insync():
    from ib_insync import IB, Stock

    return IB, Stock


def asx_stock(ticker: str) -> Any:
    _, Stock = _ib_insync()
    return Stock(symbol=ticker, exchange="SMART", currency="AUD", primaryExchange="ASX")


class IbkrDepthClient:
    def __init__(self) -> None:
        self._ib = None
        self._tickers: dict[str, object] = {}

    @property
    def ib(self) -> Any:
        if self._ib is None:
            IB, _ = _ib_insync()
            self._ib = IB()
        return self._ib

    @property
    def connected(self) -> bool:
        return self._ib is not None and self.ib.isConnected()

    async def connect(self) -> None:
        settings = get_settings().ibkr
        if self.connected:
            return
        logger.info(
            "Connecting to IB Gateway at %s:%s (clientId=%s)",
            settings.host,
            settings.port,
            settings.client_id,
        )
        await self.ib.connectAsync(
            settings.host,
            settings.port,
            clientId=settings.client_id,
            readonly=True,
        )

    def disconnect(self) -> None:
        if self.connected:
            self.ib.disconnect()

    def subscribe_depth(self, ticker: str) -> object:
        if ticker in self._tickers:
            return self._tickers[ticker]

        contract = asx_stock(ticker)
        market_ticker = self.ib.reqMktDepth(contract, numRows=5, isSmartDepth=True)
        self._tickers[ticker] = market_ticker
        logger.info("Subscribed to depth for %s", ticker)
        return market_ticker

    def unsubscribe_depth(self, ticker: str) -> None:
        if ticker not in self._tickers:
            return
        self._tickers.pop(ticker, None)
        contract = asx_stock(ticker)
        self.ib.cancelMktDepth(contract, isSmartDepth=True)
        logger.info("Unsubscribed from depth for %s", ticker)

    def sync_subscriptions(self, desired_tickers: list[str]) -> dict[str, object]:
        desired_set = set(desired_tickers)
        current_set = set(self._tickers.keys())

        for ticker in sorted(current_set - desired_set):
            self.unsubscribe_depth(ticker)

        for ticker in desired_tickers:
            self.subscribe_depth(ticker)

        return dict(self._tickers)

    def get_ticker(self, symbol: str) -> object | None:
        return self._tickers.get(symbol)
