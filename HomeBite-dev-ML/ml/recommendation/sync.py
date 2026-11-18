"""Pull the live catalogue, providers and delivered orders from the Spring services, then retrain.

Set in .env:
  MENU_SERVICE_URL, PROVIDER_SERVICE_URL, ORDER_SERVICE_URL   e.g. http://localhost:8086
  INTERNAL_SERVICE_SECRET   same value as internal.service-secret in those services
Optional:
  RECO_SYNC_HOURS           e.g. 24 -> sync in the background every 24 hours

Users are identified by an HMAC of their email (computed in order-service and user-service with the same
RECO_USER_HASH_SECRET). Raw emails, names and addresses never reach this service.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

log = logging.getLogger("reco.sync")

_HERE = Path(__file__).parent
SEED_DIR = _HERE / "data"
LIVE_DIR = _HERE / "live"

VALID_LEVELS = {"low", "medium", "high", "unknown"}
VALID_MEALS = {"breakfast", "lunch", "dinner", "snack"}

# Used when a provider has not filled in a field yet. Unknown nutrition stays "unknown", so it never
# gets a health label or a health exclusion.
TIFFIN_DEFAULTS = {
    "cuisine": "Unknown", "spice_level": 3, "meal_type": "lunch", "calories": np.nan,
    "sugar_level": "unknown", "sodium_level": "unknown", "carb_level": "unknown", "fat_level": "unknown",
    "fasting_friendly": False, "festival_tags": "", "veg": False, "provider_quality": 4.0,
    "subscription_available": False,
}


def _config() -> dict:
    return {
        "menu": os.getenv("MENU_SERVICE_URL", "").rstrip("/"),
        "provider": os.getenv("PROVIDER_SERVICE_URL", "").rstrip("/"),
        "order": os.getenv("ORDER_SERVICE_URL", "").rstrip("/"),
        "secret": os.getenv("INTERNAL_SERVICE_SECRET", ""),
    }


async def fetch_sources(client: httpx.AsyncClient, cfg: dict):
    headers = {"X-Internal-Service-Secret": cfg["secret"]}

    async def get(url: str):
        r = await client.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return r.json()

    tiffins = await get(f"{cfg['menu']}/internal/reco/tiffins")
    providers = await get(f"{cfg['provider']}/internal/reco/providers")
    orders = await get(f"{cfg['order']}/internal/reco/interactions")
    return tiffins, providers, orders


def build_frames(tiffins: list[dict], providers: list[dict], orders: list[dict]):
    """Turn the three Spring exports into the four DataFrames the recommender reads."""
    if not tiffins:
        raise ValueError("menu service returned no tiffins; keeping the previous model")

    # ---- tiffins
    t = pd.DataFrame(tiffins)
    for col, default in TIFFIN_DEFAULTS.items():
        if col not in t:
            t[col] = default
        t[col] = t[col].fillna(default)
    t["veg"] = t["veg"].astype(bool)                       # unknown veg status -> treated as non-veg (safe)
    t["fasting_friendly"] = t["fasting_friendly"].astype(bool)
    for col in ("sugar_level", "sodium_level", "carb_level", "fat_level"):
        t[col] = t[col].where(t[col].isin(VALID_LEVELS), "unknown")
    t["meal_type"] = t["meal_type"].where(t["meal_type"].isin(VALID_MEALS), "lunch")
    t["spice_level"] = pd.to_numeric(t["spice_level"], errors="coerce").fillna(3).clip(1, 5).astype(int)
    t["price"] = t["price"].astype(float)
    t["tiffin_id"] = t["tiffin_id"].astype(int)
    t["provider_id"] = t["provider_id"].astype(int)
    t = t.drop_duplicates("tiffin_id").reset_index(drop=True)

    # ---- providers (coordinates may be unknown until the provider sets them)
    p = pd.DataFrame(providers, columns=["provider_id", "area", "lat", "lng"])
    p["provider_id"] = p["provider_id"].astype(int)
    p["lat"] = pd.to_numeric(p["lat"], errors="coerce")
    p["lng"] = pd.to_numeric(p["lng"], errors="coerce")
    p["area"] = p["area"].fillna("")
    missing = sorted(set(t.provider_id) - set(p.provider_id))
    if missing:
        p = pd.concat([p, pd.DataFrame({"provider_id": missing, "area": "", "lat": np.nan, "lng": np.nan})])
    p = p.drop_duplicates("provider_id").reset_index(drop=True)

    # ---- interactions (delivered orders only; one row per dish ordered)
    i = pd.DataFrame(orders, columns=["user_id", "tiffin_id", "provider_id", "order_time", "order_type", "quantity"])
    i = i.dropna(subset=["user_id", "tiffin_id"])
    i = i.assign(
        user_id=i["user_id"].astype(str),
        tiffin_id=i["tiffin_id"].astype(int),
        provider_id=i["provider_id"].astype(int),
        order_type=i["order_type"].fillna("individual"),
        quantity=i["quantity"].fillna(1).astype(int),
        rating=np.nan,
    )
    i = i[i["tiffin_id"].isin(t["tiffin_id"])].reset_index(drop=True)
    i.insert(0, "order_id", range(1, len(i) + 1))

    # ---- users: ids only; profiles are sent with each request from the Spring preferences
    u = pd.DataFrame({"user_id": sorted(i["user_id"].unique())})
    return t, p, i, u


def write_frames(out: Path, t, p, i, u) -> None:
    out.mkdir(parents=True, exist_ok=True)
    t.to_csv(out / "tiffins.csv", index=False)
    p.to_csv(out / "providers.csv", index=False)
    i.to_csv(out / "interactions.csv", index=False)
    u.to_csv(out / "users.csv", index=False)
    shutil.copy(SEED_DIR / "festivals.csv", out / "festivals.csv")   # festival calendar is maintained by hand


async def sync_now() -> dict:
    from . import reload_recommender   # imported here to avoid a circular import at package load

    cfg = _config()
    if not all(cfg.values()):
        raise RuntimeError("Set MENU_SERVICE_URL, PROVIDER_SERVICE_URL, ORDER_SERVICE_URL and INTERNAL_SERVICE_SECRET")

    async with httpx.AsyncClient() as client:
        tiffins, providers, orders = await fetch_sources(client, cfg)
    t, p, i, u = build_frames(tiffins, providers, orders)

    # build in a temp folder first, so a failed sync never leaves half-written CSVs
    tmp = LIVE_DIR.with_name("live_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    write_frames(tmp, t, p, i, u)
    shutil.rmtree(LIVE_DIR, ignore_errors=True)
    tmp.rename(LIVE_DIR)

    reload_recommender()
    summary = {"tiffins": len(t), "providers": len(p), "interactions": len(i), "users": len(u)}
    log.info("recommendation data synced: %s", summary)
    return summary


async def sync_loop(hours: float) -> None:
    while True:
        try:
            await sync_now()
        except Exception as exc:   # keep the loop alive; the previous model keeps serving
            log.warning("recommendation sync failed: %s", exc)
        await asyncio.sleep(hours * 3600)