# routers/orders.py
import json
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from database import get_session
from dispatch import assign_order
from mapbox_client import get_route
from models import Order, Restaurant
from redis_client import (
    add_rejection,
    cache_get,
    cache_set,
    get_driver_location,
    get_trail,
    mark_driver_free,
)

router = APIRouter(prefix="/orders", tags=["orders"])


# ---------------------------- request bodies ----------------------------

class OrderIn(BaseModel):
    restaurant_id: int
    customer_lat: Optional[float] = Field(None, ge=-90, le=90)
    customer_lon: Optional[float] = Field(None, ge=-180, le=180)
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    customer_note: Optional[str] = None
    items: Optional[list[dict]] = None
    payment_type: str = Field("prepaid", pattern="^(prepaid|cod)$")
    amount_to_collect: float = Field(0.0, ge=0)
    tip: float = Field(0.0, ge=0)


class RespondIn(BaseModel):
    order_id: int
    driver_id: int
    accept: bool


class OrderDriverIn(BaseModel):
    order_id: int
    driver_id: int


class DeliveredIn(BaseModel):
    order_id: int
    driver_id: int
    delivery_code: str
    payment_mode: Optional[str] = None  # "online" | "cash"


# ------------------------------- helpers --------------------------------

def _summary(order: Order) -> dict:
    return {
        "order_id": order.id,
        "status": order.status,
        "assigned_driver": order.driver_id,
        "distance_km": order.distance_km,
        "eta_minutes": order.eta_minutes,
    }


def _target(order: Order, restaurant: Restaurant):
    """Where the driver is heading right now: restaurant first, then the customer."""
    if order.status in ("assigned", "accepted"):
        return {"stage": "to_restaurant", "lat": restaurant.lat, "lon": restaurant.lon}
    if order.status == "picked_up" and order.customer_lat is not None:
        return {"stage": "to_customer", "lat": order.customer_lat, "lon": order.customer_lon}
    return None


# ------------------------------- POST routes -------------------------------
#
# @router.post("/")
# def create_order(body: OrderIn, session: Session = Depends(get_session)):
#     restaurant = session.get(Restaurant, body.restaurant_id)
#     if not restaurant:
#         raise HTTPException(404, "Restaurant not found")
#
#     # Save first, so there's a permanent record even if no driver is free
#     order = Order(
#         **body.model_dump(),
#         status="pending",
#         delivery_code=f"{secrets.randbelow(10000):04d}",
#     )
#     session.add(order)
#     session.commit()
#     session.refresh(order)
#
#     if not assign_order(session, order):
#         return {**_summary(order), "message": "No drivers available right now - will keep retrying"}
#     return _summary(order)

#
# @router.post("/respond")
# def respond_to_order(body: RespondIn, session: Session = Depends(get_session)):
#     order = session.get(Order, body.order_id)
#     if not order or order.driver_id != body.driver_id or order.status != "assigned":
#         raise HTTPException(400, "Invalid order/driver")
#
#     if body.accept:
#         order.status = "accepted"
#         session.add(order)
#         session.commit()
#         return {"status": "accepted"}
#
#     # Rejected: free the driver, remember they said no, then immediately try the next best
#     add_rejection(order.id, body.driver_id)
#     mark_driver_free(body.driver_id)
#     order.driver_id = None
#     order.status = "pending"
#     session.add(order)
#     session.commit()
#
#     assign_order(session, order)
#     return {"rejected_by": body.driver_id, **_summary(order)}

#
# @router.post("/pickup")
# def mark_picked_up(body: OrderDriverIn, session: Session = Depends(get_session)):
#     """Driver collected the food at the restaurant -> map switches to the customer leg."""
#     order = session.get(Order, body.order_id)
#     if not order or order.driver_id != body.driver_id or order.status != "accepted":
#         raise HTTPException(400, "Invalid order/driver")
#     order.status = "picked_up"
#     session.add(order)
#     session.commit()
#     return {"status": "picked_up"}
#
#
# @router.post("/delivered")
# def mark_delivered(body: DeliveredIn, session: Session = Depends(get_session)):
#     order = session.get(Order, body.order_id)
#     if not order or order.driver_id != body.driver_id:
#         raise HTTPException(400, "Invalid order/driver")
#     if order.status != "picked_up":
#         raise HTTPException(400, "Order must be picked up before it can be delivered")
#     if order.delivery_code and body.delivery_code != order.delivery_code:
#         raise HTTPException(400, "Wrong delivery code")
#     order.status = "delivered"
#     session.add(order)
#     session.commit()
#     mark_driver_free(body.driver_id)  # driver can take new orders again
#     return {"status": "delivered"}


# -------------------------------- GET routes --------------------------------
# GET requests have no body, so these keep the order id in the URL.

@router.get("/{order_id}")
def get_order(order_id: int, session: Session = Depends(get_session)):
    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    return _summary(order)


@router.get("/{order_id}/tracking")
def tracking(order_id: int, session: Session = Depends(get_session)):
    """Cheap, poll-friendly snapshot for the map (call every ~3s)."""
    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    restaurant = session.get(Restaurant, order.restaurant_id)
    customer = (
        {"lat": order.customer_lat, "lon": order.customer_lon} if order.customer_lat is not None else None
    )
    return {
        **_summary(order),
        "driver": get_driver_location(order.driver_id) if order.driver_id else None,
        "restaurant": {"name": restaurant.name, "lat": restaurant.lat, "lon": restaurant.lon},
        "customer": customer,
        "target": _target(order, restaurant),
        "trail": get_trail(order_id),  # [[lon, lat], ...] where the driver has actually been
    }


@router.get("/{order_id}/route")
def planned_route(order_id: int, session: Session = Depends(get_session)):
    """Road path driver -> current target (Mapbox Directions). Cached 10s so many viewers = 1 API call."""
    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    restaurant = session.get(Restaurant, order.restaurant_id)
    target = _target(order, restaurant)
    driver = get_driver_location(order.driver_id) if order.driver_id else None
    if not target or not driver:
        return {"route": None}
    key = f"order:{order_id}:route:{order.status}"
    cached = cache_get(key)
    if cached:
        return json.loads(cached)
    route = get_route((driver["lon"], driver["lat"]), (target["lon"], target["lat"]))
    result = {"route": route, "stage": target["stage"]}
    cache_set(key, json.dumps(result), 10)
    return result