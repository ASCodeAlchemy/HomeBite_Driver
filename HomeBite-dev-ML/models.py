# models.py
from datetime import datetime, timezone
from typing import Optional

from pydantic import NaiveDatetime
from sqlalchemy import Column, JSON
from sqlmodel import SQLModel, Field


def utcnow() -> datetime:
    """Naive UTC timestamp (works the same on Postgres and SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Driver(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    lat: float
    lon: float
    is_available: bool = True
    rating: float = 5.0
    last_updated: NaiveDatetime = Field(default_factory=utcnow)
    external_id: Optional[str] = Field(default=None, index=True, unique=True)



class Restaurant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    lat: float
    lon: float
    address: Optional[str] = None
    phone: Optional[str] = None


class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    restaurant_id: int = Field(foreign_key="restaurant.id")
    driver_id: Optional[int] = Field(default=None, foreign_key="driver.id")
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    assigned_at: Optional[NaiveDatetime] = None
    # pending -> assigned -> accepted -> picked_up -> delivered   (or cancelled)
    status: str = "pending"
    customer_lat: Optional[float] = None
    customer_lon: Optional[float] = None
    distance_km: Optional[float] = None
    eta_minutes: Optional[float] = None

    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    customer_note: Optional[str] = None
    items: Optional[list] = Field(default=None, sa_column=Column(JSON))  # [{"name": "...", "qty": 1}]
    payment_type: str = "prepaid"          # "prepaid" | "cod"
    amount_to_collect: float = 0.0         # 0 when prepaid
    tip: float = 0.0
    delivery_code: Optional[str] = None    # 4 digits, the customer reads it to the driver