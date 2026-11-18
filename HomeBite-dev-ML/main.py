# main.py
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse

from database import init_db
from routers import drivers, orders, recommendations, restaurants


from scheduler import dispatch_loop, reco_sync_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create tables if they don't exist
    try:  # warm up the recommendation model so the first request is fast
        from ml.recommendation import get_recommender
        get_recommender()
    except Exception as exc:  # never stop dispatch because ML data is missing
        print(f"[ml] recommender not loaded: {exc}")
    task = None
    if os.getenv("ENABLE_SCHEDULER", "1") == "1":
        task = asyncio.create_task(dispatch_loop())
    sync_task = None
    if os.getenv("RECO_SYNC_ENABLED", "0") == "1":
        hours = float(os.getenv("RECO_SYNC_INTERVAL_HOURS", "24"))
        sync_task = asyncio.create_task(reco_sync_loop(hours))
    yield
    for t in (task, sync_task):
        if t:
            t.cancel()


app = FastAPI(title="HomeBite Dispatch + ML", lifespan=lifespan)
app.include_router(restaurants.router)
app.include_router(recommendations.router)


@app.get("/config")
def config():
    # PUBLIC token only (pk.*) -- it ships to the browser. Keep the secret one in MAPBOX_ACCESS_TOKEN.
    return {"mapbox_public_token": os.getenv("MAPBOX_PUBLIC_TOKEN", "")}


@app.get("/track", include_in_schema=False)
def track_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "track.html"))


@app.get("/", include_in_schema=False)
def home():
    return {
        "service": "Delivery Dispatch",
        "status": "running",
        "docs": "/docs",
        "track_example": "/track?order=1",
    }
