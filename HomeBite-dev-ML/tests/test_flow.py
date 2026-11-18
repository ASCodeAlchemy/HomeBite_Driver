from datetime import timedelta
from unittest.mock import patch, MagicMock

from sqlmodel import Session

import mapbox_client
from database import engine
from models import Order, utcnow
from scheduler import run_dispatch_cycle

REST = (19.9975, 73.7898)


def add_restaurant(client):
    return client.post("/restaurants/", json={"name": "Spice Hub", "lat": REST[0], "lon": REST[1]}).json()["id"]


def add_driver(client, name, lat, lon, rating=5.0):
    return client.post("/drivers/", json={"name": name, "lat": lat, "lon": lon, "rating": rating}).json()["id"]


def place_order(client, rid, **extra):
    return client.post("/orders/", json={"restaurant_id": rid, **extra}).json()


def respond(client, oid, did, accept):
    return client.post("/orders/respond", json={"order_id": oid, "driver_id": did, "accept": accept})


def delivery_code(oid):
    with Session(engine) as s:
        return s.get(Order, oid).delivery_code


def test_nearest_driver_wins_and_can_accept(client):
    rid = add_restaurant(client)
    near = add_driver(client, "Near", 19.9990, 73.7900)
    add_driver(client, "Far", 20.0150, 73.7700)

    res = place_order(client, rid)
    assert res["status"] == "assigned"
    assert res["assigned_driver"] == near
    assert res["distance_km"] > 0 and res["eta_minutes"] > 0

    ok = respond(client, res["order_id"], near, True)
    assert ok.json() == {"status": "accepted"}


def test_two_orders_never_get_the_same_driver(client):
    rid = add_restaurant(client)
    d1 = add_driver(client, "A", 19.9990, 73.7900)
    d2 = add_driver(client, "B", 20.0000, 73.7950)

    a = place_order(client, rid)
    b = place_order(client, rid)
    assert {a["assigned_driver"], b["assigned_driver"]} == {d1, d2}

    c = place_order(client, rid)  # nobody left
    assert c["status"] == "pending"


def test_reject_reassigns_to_someone_else(client):
    rid = add_restaurant(client)
    d1 = add_driver(client, "Near", 19.9990, 73.7900)
    d2 = add_driver(client, "Far", 20.0050, 73.7850)

    order = place_order(client, rid)
    assert order["assigned_driver"] == d1

    res = respond(client, order["order_id"], d1, False).json()
    assert res["assigned_driver"] == d2  # rejected driver is NOT offered the order again
    assert res["status"] == "assigned"


def test_pending_order_gets_retried_by_background_job(client):
    rid = add_restaurant(client)
    order = place_order(client, rid)
    assert order["status"] == "pending"

    d = add_driver(client, "Late", 19.9990, 73.7900)
    run_dispatch_cycle()
    assert client.get(f"/orders/{order['order_id']}").json()["assigned_driver"] == d


def test_timeout_moves_order_to_next_driver(client):
    rid = add_restaurant(client)
    d1 = add_driver(client, "Slow", 19.9990, 73.7900)
    d2 = add_driver(client, "Backup", 20.0050, 73.7850)

    order = place_order(client, rid)
    assert order["assigned_driver"] == d1

    with Session(engine) as s:  # pretend 60s went by without an answer
        o = s.get(Order, order["order_id"])
        o.assigned_at = utcnow() - timedelta(seconds=60)
        s.add(o); s.commit()

    run_dispatch_cycle()
    assert client.get(f"/orders/{order['order_id']}").json()["assigned_driver"] == d2


def test_delivered_frees_the_driver(client):
    rid = add_restaurant(client)
    d = add_driver(client, "Solo", 19.9990, 73.7900)
    o1 = place_order(client, rid)
    oid = o1["order_id"]
    respond(client, oid, d, True)
    client.post("/orders/pickup", json={"order_id": oid, "driver_id": d})
    r = client.post("/orders/delivered", json={"order_id": oid, "driver_id": d, "delivery_code": delivery_code(oid)})
    assert r.json() == {"status": "delivered"}

    o2 = place_order(client, rid)
    assert o2["assigned_driver"] == d


def test_unknown_restaurant_is_404(client):
    assert client.post("/orders/", json={"restaurant_id": 999}).status_code == 404


def test_mapbox_response_is_parsed_correctly(monkeypatch):
    monkeypatch.setenv("MAPBOX_ACCESS_TOKEN", "fake")
    fake = MagicMock()
    fake.json.return_value = {"distances": [[1850.0, None]], "durations": [[312.0, None]]}
    fake.raise_for_status.return_value = None
    with patch.object(mapbox_client.requests, "get", return_value=fake) as g:
        out = mapbox_client.get_real_distances((73.78, 19.99), [(73.79, 19.99), (73.80, 19.99)])

    assert out[0] == {"driver_index": 0, "distance_km": 1.85, "duration_min": 5.2}
    assert out[1]["distance_km"] is None  # unroutable point handled
    assert g.call_args.kwargs["params"]["sources"] == "0"
    assert g.call_args.kwargs["params"]["destinations"] == "1;2"


def test_wrong_delivery_code_is_rejected(client):
    rid = add_restaurant(client)
    d = add_driver(client, "Solo", 19.9990, 73.7900)
    oid = place_order(client, rid)["order_id"]
    respond(client, oid, d, True)
    client.post("/orders/pickup", json={"order_id": oid, "driver_id": d})

    code = delivery_code(oid)
    wrong = f"{(int(code) + 1) % 10000:04d}"
    bad = client.post("/orders/delivered", json={"order_id": oid, "driver_id": d, "delivery_code": wrong})
    assert bad.status_code == 400
    assert client.get(f"/orders/{oid}").json()["status"] == "picked_up"


def test_cannot_deliver_before_pickup(client):
    rid = add_restaurant(client)
    d = add_driver(client, "Solo", 19.9990, 73.7900)
    oid = place_order(client, rid)["order_id"]
    respond(client, oid, d, True)
    r = client.post("/orders/delivered", json={"order_id": oid, "driver_id": d, "delivery_code": delivery_code(oid)})
    assert r.status_code == 400


def test_offline_driver_is_skipped_and_returns_online(client):
    rid = add_restaurant(client)
    d = add_driver(client, "Solo", 19.9990, 73.7900)
    client.post("/drivers/offline", json={"driver_id": d})
    assert place_order(client, rid)["status"] == "pending"

    client.post("/drivers/online", json={"driver_id": d})
    run_dispatch_cycle()
    assert client.get("/orders/1").json()["assigned_driver"] == d


def test_full_tracking_flow(client):
    rid = add_restaurant(client)
    did = add_driver(client, "Ravi", 19.9990, 73.7900)
    oid = place_order(client, rid, customer_lat=20.01, customer_lon=73.80)["order_id"]

    respond(client, oid, did, True)
    client.post("/drivers/location", json={"driver_id": did, "lat": 19.9985, "lon": 73.7895})
    client.post("/drivers/location", json={"driver_id": did, "lat": 19.9980, "lon": 73.7892})

    t = client.get(f"/orders/{oid}/tracking").json()
    assert t["target"]["stage"] == "to_restaurant"
    assert len(t["trail"]) == 2 and abs(t["driver"]["lat"] - 19.998) < 1e-4
    assert client.get(f"/orders/{oid}/route").json()["route"]["geometry"]["type"] == "LineString"

    assert client.post("/orders/pickup", json={"order_id": oid, "driver_id": did}).json() == {"status": "picked_up"}
    assert client.get(f"/orders/{oid}/tracking").json()["target"]["stage"] == "to_customer"

    done = client.post("/orders/delivered", json={"order_id": oid, "driver_id": did, "delivery_code": delivery_code(oid)})
    assert done.json() == {"status": "delivered"}
    client.post("/drivers/location", json={"driver_id": did, "lat": 20.0, "lon": 73.8})
    assert len(client.get(f"/orders/{oid}/tracking").json()["trail"]) == 2  # no pings recorded after delivery
