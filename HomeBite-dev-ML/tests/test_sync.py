import numpy as np
import pytest

from ml.recommendation.recommender import Recommender
from ml.recommendation.sync import build_frames, write_frames


def _messy_sources():
    """Real-world shape: providers have not filled in most metadata yet."""
    tiffins = [
        {"tiffin_id": 1, "provider_id": 10, "dish_name": "Dal Rice", "veg": True, "price": 90,
         "cuisine": None, "spice_level": None, "meal_type": None, "calories": None, "sugar_level": None,
         "sodium_level": None, "carb_level": None, "fat_level": None, "fasting_friendly": None,
         "festival_tags": None, "provider_quality": None},
        {"tiffin_id": 2, "provider_id": 10, "dish_name": "Chicken Curry", "veg": False, "price": 180,
         "cuisine": "North Indian", "spice_level": 4, "meal_type": "dinner", "calories": 800,
         "sugar_level": "low", "sodium_level": "high", "carb_level": "medium", "fat_level": "high",
         "fasting_friendly": False, "festival_tags": "", "provider_quality": 4.5},
        {"tiffin_id": 3, "provider_id": 11, "dish_name": "Sabudana Vada", "veg": True, "price": 80,
         "cuisine": "Maharashtrian", "spice_level": 2, "meal_type": "breakfast", "calories": 420,
         "sugar_level": "low", "sodium_level": "medium", "carb_level": "high", "fat_level": "high",
         "fasting_friendly": True, "festival_tags": "navratri", "provider_quality": 4.0},
    ]
    providers = [{"provider_id": 10, "area": "Kothrud", "lat": 18.55, "lng": 73.80}]  # provider 11: no coords
    orders = ([{"user_id": "hash-abc", "tiffin_id": 1, "provider_id": 10, "order_time": "2026-10-01T12:00:00",
                "order_type": "individual", "quantity": 1}] * 6
              + [{"user_id": "hash-xyz", "tiffin_id": 2, "provider_id": 10, "order_time": "2026-10-02T20:00:00",
                  "order_type": "individual", "quantity": 1}])
    return tiffins, providers, orders


def _train(tmp_path):
    t, p, i, u = build_frames(*_messy_sources())
    write_frames(tmp_path, t, p, i, u)
    return Recommender(tmp_path)


def test_messy_data_trains_without_nan(tmp_path):
    r = _train(tmp_path)
    assert np.isfinite(r.item_feats).all()
    out = r.recommend(user_id="hash-abc", top_k=3, lat=18.55, lng=73.80)
    assert out["mode"] == "hybrid"
    assert out["items"] and all(np.isfinite(i["score"]) for i in out["items"])


def test_radius_excludes_provider_without_coordinates(tmp_path):
    r = _train(tmp_path)
    out = r.recommend(user_id="hash-xyz", top_k=10, lat=18.55, lng=73.80, radius_km=50)
    assert all(i["provider_id"] == 10 for i in out["items"])


def test_unverified_dish_gets_no_health_label(tmp_path):
    r = _train(tmp_path)
    out = r.recommend(profile=None, top_k=10, health_conditions=["diabetes"])
    dal = [i for i in out["items"] if i["dish_name"] == "Dal Rice"]
    assert dal and "Lower sugar" not in dal[0]["reasons"]


def test_string_and_numeric_user_ids_both_work(tmp_path):
    r = _train(tmp_path)
    assert r.recommend(user_id="hash-abc", top_k=2)["mode"] == "hybrid"
    assert r.recommend(user_id=None, top_k=2)["mode"] in ("popular", "profile")


def test_empty_menu_is_refused():
    with pytest.raises(ValueError):
        build_frames([], [], [])


def test_unknown_veg_is_treated_as_non_veg(tmp_path):
    t, *_ = build_frames(*_messy_sources())
    assert bool(t.loc[t.tiffin_id == 1, "veg"].iloc[0]) is True   # Dal Rice was explicitly veg
    tiffins, providers, orders = _messy_sources()
    tiffins[0]["veg"] = None
    t2, *_ = build_frames(tiffins, providers, orders)
    assert bool(t2.loc[t2.tiffin_id == 1, "veg"].iloc[0]) is False