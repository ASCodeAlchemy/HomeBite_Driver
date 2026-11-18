import pytest
from fastapi.testclient import TestClient

KEY = {"X-Reco-Api-Key": "testkey"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("RECO_API_KEY", "testkey")
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    from main import app
    with TestClient(app) as c:
        yield c


def test_requires_key(client):
    assert client.post("/recommend", json={"user_id": 1}).status_code == 401
    assert client.post("/recommend", json={}, headers={"X-Reco-Api-Key": "bad"}).status_code == 401


def test_health_is_open(client):
    r = client.get("/recommend/health")
    assert r.status_code == 200 and r.json()["items"] > 0


def test_known_user_hybrid(client):
    r = client.post("/recommend", json={"user_id": 1, "top_k": 5}, headers=KEY).json()
    assert r["mode"] == "hybrid" and len(r["items"]) == 5


def test_veg_profile_never_returns_non_veg(client):
    body = {"profile": {"diet_type": "veg", "fav_cuisines": ["Maharashtrian"]}, "top_k": 20}
    r = client.post("/recommend", json=body, headers=KEY).json()
    assert r["mode"] == "profile" and r["items"] and all(i["veg"] for i in r["items"])


def test_filters(client):
    r = client.post("/recommend", json={"user_id": 1, "meal_type": "dinner", "max_price": 150, "top_k": 20}, headers=KEY).json()
    assert r["items"] and all(i["meal_type"] == "dinner" and i["price"] <= 150 for i in r["items"])


def test_validation(client):
    assert client.post("/recommend", json={"profile": {"diet_type": "banana"}}, headers=KEY).status_code == 422


# ---------------- festivals ----------------

def test_festival_boost_only_inside_window(client):
    body = {"user_id": 1, "top_k": 10}
    during = client.post("/recommend", json={**body, "on_date": "2026-10-15"}, headers=KEY).json()
    outside = client.post("/recommend", json={**body, "on_date": "2026-08-01"}, headers=KEY).json()
    assert during["context"]["active_festivals"] == ["Navratri"]
    assert outside["context"]["active_festivals"] == []
    assert any("Navratri special" in i["reasons"] for i in during["items"])
    assert not any("Navratri special" in i["reasons"] for i in outside["items"])


def test_fasting_mode_only_returns_vrat_items(client):
    body = {"user_id": 1, "top_k": 20, "on_date": "2026-10-15", "observe_fasting": True}
    r = client.post("/recommend", json=body, headers=KEY).json()
    assert r["context"]["fasting_mode"] and r["items"]
    assert all("Navratri special" in i["reasons"] for i in r["items"])


def test_fasting_flag_ignored_when_no_fasting_festival(client):
    body = {"user_id": 1, "top_k": 5, "on_date": "2026-11-07", "observe_fasting": True}  # Diwali: not a fast
    r = client.post("/recommend", json=body, headers=KEY).json()
    assert r["context"]["fasting_mode"] is False and len(r["items"]) == 5


# ---------------- health ----------------

def _tiffins():
    import pandas as pd
    from ml.recommendation import DATA_DIR
    return pd.read_csv(DATA_DIR / "tiffins.csv").set_index("tiffin_id")


def test_diabetes_excludes_high_sugar(client):
    t = _tiffins()
    r = client.post("/recommend", json={"user_id": 2, "top_k": 50, "health_conditions": ["diabetes"],
                                        "on_date": "2026-11-07"}, headers=KEY).json()
    assert r["items"] and all(t.loc[i["tiffin_id"], "sugar_level"] != "high" for i in r["items"])
    assert r["context"]["notice"]


def test_hypertension_excludes_high_sodium(client):
    t = _tiffins()
    r = client.post("/recommend", json={"user_id": 3, "top_k": 50, "health_conditions": ["hypertension"]}, headers=KEY).json()
    assert r["items"] and all(t.loc[i["tiffin_id"], "sodium_level"] != "high" for i in r["items"])


def test_unknown_condition_is_ignored_not_fatal(client):
    r = client.post("/recommend", json={"user_id": 1, "health_conditions": ["made_up"]}, headers=KEY).json()
    assert r["context"]["health_ignored"] == ["made_up"] and r["items"]


# ---------------- feed ----------------

def test_feed_sections_have_no_duplicates(client):
    body = {"user_id": 1, "on_date": "2026-11-07", "health_conditions": ["diabetes"], "lat": 18.55, "lng": 73.8}
    r = client.post("/recommend/feed", json=body, headers=KEY).json()
    keys = [s["key"] for s in r["sections"]]
    assert keys == ["festival", "health", "for_you", "popular_nearby"]
    ids = [i["tiffin_id"] for s in r["sections"] for i in s["items"]]
    assert len(ids) == len(set(ids))


def test_feed_has_no_festival_row_off_season(client):
    r = client.post("/recommend/feed", json={"user_id": 1, "on_date": "2026-08-01"}, headers=KEY).json()
    assert [s["key"] for s in r["sections"]] == ["for_you"]


def test_options_lists_conditions(client):
    assert "diabetes" in client.get("/recommend/options").json()["health_conditions"]
