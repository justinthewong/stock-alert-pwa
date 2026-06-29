from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.database import Alert


@dataclass(frozen=True)
class DepthEvaluation:
    available: int
    triggered: bool


def buy_liquidity(dom_asks: Iterable, target_price: float) -> int:
    return int(sum(level.size for level in dom_asks if level.price <= target_price))


def sell_liquidity(dom_bids: Iterable, target_price: float) -> int:
    return int(sum(level.size for level in dom_bids if level.price >= target_price))


def evaluate_alert(alert: Alert, dom_bids: Iterable, dom_asks: Iterable) -> DepthEvaluation:
    if alert.side == "buy":
        available = buy_liquidity(dom_asks, alert.target_price)
    else:
        available = sell_liquidity(dom_bids, alert.target_price)
    return DepthEvaluation(available=available, triggered=available >= alert.share_count)
