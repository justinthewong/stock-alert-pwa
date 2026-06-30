from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Side = Literal["buy", "sell"]
AlertStatus = Literal["active", "triggered", "disabled"]
IbkrConnectionStatus = Literal["disconnected", "connecting", "connected", "error"]


class LoginRequest(BaseModel):
    username: str
    password: str


class AlertCreate(BaseModel):
    ticker: str = Field(min_length=2, max_length=6)
    side: Side
    share_count: int = Field(gt=0)
    target_price: float = Field(gt=0)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        ticker = value.strip().upper()
        if not ticker.isalpha():
            raise ValueError("Ticker must contain letters only.")
        return ticker


class AlertResponse(BaseModel):
    id: int
    ticker: str
    side: Side
    share_count: int
    target_price: float
    status: AlertStatus
    last_checked_at: datetime | None = None
    triggered_at: datetime | None = None
    last_depth_json: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionRequest(BaseModel):
    endpoint: str
    keys: PushSubscriptionKeys


class PublicConfigResponse(BaseModel):
    vapid_public_key: str


class IbkrStatusResponse(BaseModel):
    status: IbkrConnectionStatus
    message: str
    gateway_running: bool
