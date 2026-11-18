# dispatch.py
from typing import Optional

from sqlmodel import Session

from mapbox_client import get_real_distances
from models import Driver, Order, Restaurant, utcnow
from redis_client import (
    get_nearby_drivers,
    get_rejections,
    is_driver_busy,
    mark_driver_free,
    set_driver_order,
    try_lock_driver,
    try_lock_order,
    unlock_order,
)

MAX_DRIVERS_PER_MATRIX_CALL = 15  # stay safely under Mapbox's 25-coordinate cap


def find_best_driver(
    session: Session,
    restaurant_lat: float,
    restaurant_lon: float,
    radius_km: float = 3,
    exclude: Optional[set[int]] = None,
):
    """Returns (driver_id, distance_km, eta_min, score) with the driver already locked, or None."""
    exclude = exclude or set()

    # Step A: cheap shortlist from Redis (straight-line distance, nearest first)
    candidates = get_nearby_drivers(restaurant_lat, restaurant_lon, radius_km)

    available = []
    live = {}  # driver_id -> (lon, lat) straight from Redis
    for driver_id_str, _dist, coord in candidates:
        driver_id = int(driver_id_str)
        if driver_id in exclude or is_driver_busy(driver_id):
            continue
        driver = session.get(Driver, driver_id)
        if not driver or not driver.is_available:
            continue
        available.append(driver)
        live[driver_id] = coord
        if len(available) >= MAX_DRIVERS_PER_MATRIX_CALL:
            break

    if not available:
        return None

    # Step B: ONE Mapbox call for the whole shortlist
    origin = (restaurant_lon, restaurant_lat)
    destinations = [live[d.id] for d in available]
    route_results = get_real_distances(origin, destinations)

    # Step C: score = real ETA (70%) + rating (30%)
    scored = []
    for result in route_results:
        if result["distance_km"] is None:
            continue
        driver = available[result["driver_index"]]
        eta_min = result["duration_min"]
        score = (1 / (eta_min + 1)) * 0.7 + (driver.rating / 5) * 0.3
        scored.append((driver.id, result["distance_km"], eta_min, score))

    if not scored:
        return None

    # Step D: walk down the ranking until one lock succeeds
    scored.sort(key=lambda x: x[3], reverse=True)
    for driver_id, distance_km, eta_min, score in scored:
        if try_lock_driver(driver_id):
            return driver_id, distance_km, eta_min, score

    return None


def assign_order(session: Session, order: Order) -> bool:
    """Try to give a pending order to the best driver. Returns True if assigned."""
    if not try_lock_order(order.id):  # someone else is already assigning this order
        return False
    try:
        session.refresh(order)
        if order.status != "pending":
            return False

        restaurant = session.get(Restaurant, order.restaurant_id)
        if not restaurant:
            return False
        result = find_best_driver(
            session, restaurant.lat, restaurant.lon, exclude=get_rejections(order.id)
        )
        if not result:
            return False

        driver_id, distance_km, eta_min, _score = result
        try:
            order.driver_id = driver_id
            order.status = "assigned"
            order.assigned_at = utcnow()
            order.distance_km = distance_km
            order.eta_minutes = eta_min
            session.add(order)
            session.commit()
        except Exception:
            # DB write failed -> don't leave the driver locked forever
            session.rollback()
            mark_driver_free(driver_id)
            raise
        set_driver_order(driver_id, order.id)  # location pings now get recorded as this order's trail
        session.refresh(order)
        return True
    finally:
        unlock_order(order.id)
