"""Fake driver app: drives the whole job so you can watch /track without a phone.
Usage: python simulate_driver.py <order_id> <driver_id> [base_url]"""
import sys
import time

import requests

order_id, driver_id = int(sys.argv[1]), int(sys.argv[2])
BASE = sys.argv[3] if len(sys.argv) > 3 else "http://localhost:8000"


def densify(coords, min_points=40):
    """Mapbox gives many points; the straight-line fallback only 2 -> add steps in between."""
    if len(coords) >= min_points:
        return coords
    per = max(2, min_points // max(1, len(coords) - 1))
    out = []
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        out += [[x1 + (x2 - x1) * i / per, y1 + (y2 - y1) * i / per] for i in range(per)]
    return out + [coords[-1]]


def drive_leg():
    leg = requests.get(f"{BASE}/orders/{order_id}/route").json()
    if not leg.get("route"):
        return False
    for lon, lat in densify(leg["route"]["geometry"]["coordinates"]):
        requests.post(f"{BASE}/drivers/location", json={"driver_id": driver_id, "lat": lat, "lon": lon})
        time.sleep(1)
    return True


offer = requests.get(f"{BASE}/drivers/offer", params={"driver_id": driver_id}).json().get("offer")
if not offer or offer["order_id"] != order_id:
    sys.exit(f"Order {order_id} is not currently offered to driver {driver_id}")
code = offer["delivery_code"]  # dev only: a real customer reads this out to the driver

requests.post(f"{BASE}/orders/respond", json={"order_id": order_id, "driver_id": driver_id, "accept": True}).raise_for_status()
print("accepted -> driving to restaurant")
drive_leg()
requests.post(f"{BASE}/orders/pickup", json={"order_id": order_id, "driver_id": driver_id}).raise_for_status()
print("picked up -> driving to customer")
drive_leg()
requests.post(
    f"{BASE}/orders/delivered",
    json={"order_id": order_id, "driver_id": driver_id, "delivery_code": code},
).raise_for_status()
print("delivered")
