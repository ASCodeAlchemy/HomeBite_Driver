# redis_client.py
import os

import redis
from dotenv import load_dotenv

load_dotenv()

r = redis.Redis.from_url(
    os.getenv("REDIS_URL") or "redis://localhost:6379/0", decode_responses=True
)

LOCATIONS_KEY = "drivers:locations"
BUSY_KEY = "drivers:busy"


# ---------- driver locations ----------
def update_driver_location(driver_id: int, lat: float, lon: float):
    # Redis GEO takes (longitude, latitude, member) -- note the order!
    r.geoadd(LOCATIONS_KEY, (lon, lat, str(driver_id)))

def remove_driver_location(driver_id: int):
    """Take a driver off the live map (used when they go offline)."""
    r.zrem(LOCATIONS_KEY, str(driver_id))


def get_nearby_drivers(lat: float, lon: float, radius_km: float = 3):
    """Returns [(driver_id_str, distance_km, (lon, lat)), ...] nearest first."""
    return r.geosearch(
        LOCATIONS_KEY,
        longitude=lon,
        latitude=lat,
        radius=radius_km,
        unit="km",
        withdist=True,
        withcoord=True,  # live position, so Mapbox measures from where the driver IS
        sort="ASC",
    )


# ---------- driver locking ----------
def try_lock_driver(driver_id: int) -> bool:
    """Atomically claim a driver. False means someone else already has them."""
    return r.sadd(BUSY_KEY, driver_id) == 1


def mark_driver_free(driver_id: int):
    r.srem(BUSY_KEY, driver_id)
    r.delete(f"driver:{driver_id}:order")


def is_driver_busy(driver_id: int) -> bool:
    return bool(r.sismember(BUSY_KEY, driver_id))


# ---------- drivers who rejected / timed out on an order ----------
def add_rejection(order_id: int, driver_id: int):
    key = f"order:{order_id}:rejected"
    r.sadd(key, driver_id)
    r.expire(key, 3600)


def get_rejections(order_id: int) -> set[int]:
    return {int(x) for x in r.smembers(f"order:{order_id}:rejected")}


# ---------- per-order lock (stops two workers assigning the same order) ----------
def try_lock_order(order_id: int) -> bool:
    return bool(r.set(f"order:{order_id}:assigning", 1, nx=True, ex=15))


def unlock_order(order_id: int):
    r.delete(f"order:{order_id}:assigning")


# ---------- live tracking ----------
def get_driver_location(driver_id: int):
    pos = r.geopos(LOCATIONS_KEY, str(driver_id))[0]
    return {"lat": pos[1], "lon": pos[0]} if pos else None


def set_driver_order(driver_id: int, order_id: int):
    r.set(f"driver:{driver_id}:order", order_id, ex=6 * 3600)


def get_driver_order(driver_id: int):
    v = r.get(f"driver:{driver_id}:order")
    return int(v) if v else None


def append_trail(order_id: int, lat: float, lon: float):
    """Breadcrumb of where the driver really went for this order."""
    key = f"order:{order_id}:trail"
    r.rpush(key, f"{lon},{lat}")
    r.ltrim(key, -3000, -1)
    r.expire(key, 24 * 3600)


def get_trail(order_id: int) -> list[list[float]]:
    return [[float(v) for v in p.split(",")] for p in r.lrange(f"order:{order_id}:trail", 0, -1)]


def cache_get(key: str):
    return r.get(key)


def cache_set(key: str, value: str, ttl: int):
    r.set(key, value, ex=ttl)
