"""Food recommendation model. Public surface: get_recommender(), reload_recommender(), Profile."""
import os
import threading
from pathlib import Path

from .recommender import Profile, Recommender

_HERE = Path(__file__).parent


def data_dir() -> Path:
    """RECO_DATA_DIR if set; else the live sync output if it exists; else the bundled seed data."""
    env = os.getenv("RECO_DATA_DIR", "").strip()   # a blank line in .env means "not set"
    if env:
        return Path(env)
    live = _HERE / "live"
    return live if (live / "tiffins.csv").exists() else _HERE / "data"


DATA_DIR = data_dir()

_lock = threading.Lock()
_instance: Recommender | None = None


def get_recommender() -> Recommender:
    """Lazy singleton; trains on first use."""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = Recommender(data_dir())
    return _instance


def reload_recommender() -> Recommender:
    """Retrain from the current data and swap atomically; in-flight requests keep the old model."""
    global _instance
    new = Recommender(data_dir())
    with _lock:
        _instance = new
    return new


__all__ = ["Profile", "Recommender", "get_recommender", "reload_recommender", "data_dir", "DATA_DIR"]