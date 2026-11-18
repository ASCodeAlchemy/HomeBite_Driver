import os
import secrets
from datetime import date
from typing import Optional, Union

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from ml.recommendation.sync import sync_now
from ml.recommendation import Profile, get_recommender, reload_recommender
from ml.recommendation.rules import SUPPORTED_CONDITIONS

router = APIRouter(prefix="/recommend", tags=["recommendations"])


def require_reco_key(x_reco_api_key: str = Header(default="")):
    """Service-to-service auth: Spring Boot sends X-Reco-Api-Key."""
    expected = os.getenv("RECO_API_KEY", "")
    if not expected or not secrets.compare_digest(x_reco_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")


class ProfileIn(BaseModel):
    diet_type: Optional[str] = Field(None, pattern="^(veg|non_veg|eggetarian|jain)$")
    spice_pref: Optional[int] = Field(None, ge=1, le=5)
    budget_per_meal: Optional[float] = Field(None, gt=0)
    fav_cuisines: Optional[list[str]] = None
    preferred_meal: Optional[str] = Field(None, pattern="^(breakfast|lunch|dinner)$")


class RecommendIn(BaseModel):
    # string ids in real data (hashed on the Spring side); ints accepted for the synthetic demo
    user_id: Optional[Union[str, int]] = None
    profile: Optional[ProfileIn] = None
    top_k: int = Field(10, ge=1, le=50)
    meal_type: Optional[str] = Field(None, pattern="^(breakfast|lunch|dinner)$")
    max_price: Optional[float] = Field(None, gt=0)
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)
    radius_km: Optional[float] = Field(None, gt=0)
    exclude_tiffin_ids: list[int] = []
    # --- derived flags from Spring Boot (NOT raw medical records) ---
    health_conditions: list[str] = []          # e.g. ["diabetes", "hypertension"]; see /recommend/options
    observe_fasting: bool = False              # user opted in to vrat/fasting mode
    on_date: Optional[date] = None             # defaults to today; handy for testing festivals
    section_size: int = Field(6, ge=1, le=20)  # /recommend/feed only


# NOTE: plain `def` (not async) on purpose -- the model does CPU work with numpy,
# and FastAPI runs sync endpoints in a thread pool so the event loop (and the
# dispatch scheduler) are never blocked.

@router.post("", dependencies=[Depends(require_reco_key)])
def recommend(req: RecommendIn):
    return get_recommender().recommend(top_k=req.top_k, **_kwargs(req))


def _kwargs(req: RecommendIn):
    return dict(
        user_id=None if req.user_id is None else str(req.user_id),
        profile=Profile(**req.profile.model_dump()) if req.profile else None,
        meal_type=req.meal_type, max_price=req.max_price, lat=req.lat, lng=req.lng,
        radius_km=req.radius_km, exclude_ids=set(req.exclude_tiffin_ids),
        health_conditions=req.health_conditions, today=req.on_date, observe_fasting=req.observe_fasting,
    )


@router.post("/feed", dependencies=[Depends(require_reco_key)])
def feed(req: RecommendIn):
    """Explore / dashboard feed: festival row, health row, 'for you' row, popular-near-you row."""
    return get_recommender().feed(top_k=req.top_k, section_size=req.section_size, **_kwargs(req))


@router.get("/options")
def options():
    """What the app can offer in its preferences screen."""
    r = get_recommender()
    return {"health_conditions": SUPPORTED_CONDITIONS,
            "festivals": r.festivals[["name", "start", "end"]].astype(str).to_dict("records")}


@router.get("/health")
def recommend_health():
    r = get_recommender()
    return {"status": "ok", "users": len(r.user_ids), "items": len(r.item_ids)}


@router.post("/retrain", dependencies=[Depends(require_reco_key)])
def retrain():
    r = reload_recommender()
    return {"status": "retrained", "interactions": len(r.interactions)}


@router.post("/sync", dependencies=[Depends(require_reco_key)])
async def sync():
    """Pull real tiffins, providers and delivered orders from Spring, then retrain."""
    try:
        return await sync_now()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"sync failed: {exc}")