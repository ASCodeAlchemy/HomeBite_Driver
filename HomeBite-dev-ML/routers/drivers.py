# routers/drivers.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database import get_session
from models import Driver, Order, Restaurant
from redis_client import (
    append_trail,
    get_driver_location,
    get_driver_order,
    remove_driver_location,
    update_driver_location,
)
from sqlalchemy.exc import IntegrityError   # with the other imports
router = APIRouter(prefix="/drivers", tags=["drivers"])


# ---------------------------- request bodies ----------------------------
class DriverSyncIn(BaseModel):              # above class LocationIn
    external_id: str = Field(min_length=1)  # the driver's identity in Spring Boot
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)

class DriverIn(BaseModel):
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    rating: float = Field(5.0, ge=0, le=5)


class LocationIn(BaseModel):
    driver_id: int
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class DriverRef(BaseModel):
    driver_id: int


# ------------------------------- routes -------------------------------

@router.post("/")
def create_driver(body: DriverIn, session: Session = Depends(get_session)):
    driver = Driver(name=body.name, lat=body.lat, lon=body.lon, rating=body.rating)
    session.add(driver)
    session.commit()
    session.refresh(driver)
    update_driver_location(driver.id, body.lat, body.lon)  # so they show up in searches immediately
    return driver


@router.post("/sync")                       # above @router.post("/location")
def sync_driver(body: DriverSyncIn, session: Session = Depends(get_session)):
    """Find-or-create the dispatch driver for a Spring Boot driver. Safe to call every time."""
    def find():
        return session.exec(
            select(Driver).where(Driver.external_id == body.external_id)
        ).first()

    driver = find()
    if driver:
        if driver.name != body.name:
            driver.name = body.name
            session.add(driver)
            session.commit()
            session.refresh(driver)
    else:
        driver = Driver(
            name=body.name, lat=body.lat, lon=body.lon, external_id=body.external_id
        )
        session.add(driver)
        try:
            session.commit()
        except IntegrityError:  # two requests created it at the same moment
            session.rollback()
            driver = find()
        else:
            session.refresh(driver)

    if not get_driver_location(driver.id):  # make sure they can be found by dispatch
        update_driver_location(driver.id, body.lat, body.lon)
    return driver

@router.post("/location")
def update_location(body: LocationIn):
    # Deliberately thin: no DB write, straight to Redis (hit every 3-5s per driver)
    update_driver_location(body.driver_id, body.lat, body.lon)
    order_id = get_driver_order(body.driver_id)
    if order_id:  # driver is on a job -> keep the breadcrumb trail
        append_trail(order_id, body.lat, body.lon)
    return {"status": "updated", "driver_id": body.driver_id}


@router.post("/online")
def go_online(body: DriverRef, session: Session = Depends(get_session)):
    driver = session.get(Driver, body.driver_id)
    if not driver:
        raise HTTPException(404, "Driver not found")
    driver.is_available = True
    session.add(driver)
    session.commit()
    if not get_driver_location(driver.id):  # going offline removed them from the live map
        update_driver_location(driver.id, driver.lat, driver.lon)
    return {"status": "online", "driver_id": body.driver_id}


@router.post("/offline")
def go_offline(body: DriverRef, session: Session = Depends(get_session)):
    driver = session.get(Driver, body.driver_id)
    if not driver:
        raise HTTPException(404, "Driver not found")
    if get_driver_order(body.driver_id):  # assigned, accepted or picked up
        raise HTTPException(400, "Finish the active order before going offline")
    driver.is_available = False
    session.add(driver)
    session.commit()
    remove_driver_location(body.driver_id)  # disappears from the live map
    return {"status": "offline", "driver_id": body.driver_id}


@router.get("/offer")
def get_offer(driver_id: int, session: Session = Depends(get_session)):
    """The driver app polls this every ~3s to see if an order has been assigned to it."""
    order = session.exec(
        select(Order).where(Order.driver_id == driver_id, Order.status == "assigned")
    ).first()
    if not order:
        return {"offer": None}

    r = session.get(Restaurant, order.restaurant_id)
    fare = round(40 + (order.distance_km or 3) * 5.6) + int(order.tip)
    return {
        "offer": {
            "order_id": order.id,
            "distance_km": order.distance_km,
            "eta_minutes": order.eta_minutes,
            "fare": fare,
            "tip": order.tip,
            "payment_type": order.payment_type,
            "amount_to_collect": order.amount_to_collect,
            "items": order.items or [],
            "restaurant": {
                "name": r.name,
                "address": r.address,
                "phone": r.phone,
                "lat": r.lat,
                "lon": r.lon,
            },
            "customer": {
                "name": order.customer_name,
                "address": order.customer_address,
                "phone": order.customer_phone,
                "note": order.customer_note,
                "lat": order.customer_lat,
                "lon": order.customer_lon,
            },
            "delivery_code": order.delivery_code,  # dev only: remove for production
        }
    }