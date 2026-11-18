# mapbox_client.py
import logging
import math
import os

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("dispatch.mapbox")

MAPBOX_MATRIX_URL = "https://api.mapbox.com/directions-matrix/v1/mapbox/driving"
MAPBOX_DIRECTIONS_URL = "https://api.mapbox.com/directions/v5/mapbox/driving"


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def _fmt(lon: float, lat: float) -> str:
    """Mapbox can reject very long coordinates, so round to 6 decimals (~10 cm)."""
    return f"{lon:.6f},{lat:.6f}"


def _estimate(origin: tuple, destinations: list[tuple]) -> list[dict]:
    """Fallback when Mapbox is unavailable: straight line x 1.3 for road detours, 25 km/h."""
    results = []
    for i, (lon, lat) in enumerate(destinations):
        km = _haversine_km(origin[1], origin[0], lat, lon) * 1.3
        results.append(
            {"driver_index": i, "distance_km": round(km, 2), "duration_min": round(km / 25 * 60, 2)}
        )
    return results


def get_real_distances(origin: tuple, destinations: list[tuple]) -> list[dict]:
    """
    origin:       (lon, lat) of the restaurant
    destinations: [(lon, lat), ...] of candidate drivers -- max 24 (Mapbox allows 25 coordinates total)
    Returns:      [{driver_index, distance_km, duration_min}, ...]
    """
    token = os.getenv("MAPBOX_ACCESS_TOKEN")
    if not token:
        log.warning("No MAPBOX_ACCESS_TOKEN set - using straight-line estimates")
        return _estimate(origin, destinations)

    all_coords = [origin] + destinations
    coord_str = ";".join(_fmt(lon, lat) for lon, lat in all_coords)
    destination_indices = ";".join(str(i) for i in range(1, len(all_coords)))

    params = {
        "sources": "0",
        "destinations": destination_indices,
        "annotations": "distance,duration",
        "access_token": token,
    }
    try:
        response = requests.get(f"{MAPBOX_MATRIX_URL}/{coord_str}", params=params, timeout=10)
        if not response.ok:
            # Mapbox's own explanation of what it didn't like
            log.warning("Mapbox matrix %s: %s", response.status_code, response.text[:300])
        response.raise_for_status()
        data = response.json()
        distances = data["distances"][0]
        durations = data["durations"][0]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        # type(exc).__name__ only: str(exc) contains the URL, which contains the token
        log.warning("Mapbox call failed (%s) - using straight-line estimates", type(exc).__name__)
        return _estimate(origin, destinations)

    results = []
    for i, (dist_m, dur_s) in enumerate(zip(distances, durations)):
        results.append(
            {
                "driver_index": i,
                "distance_km": round(dist_m / 1000, 2) if dist_m is not None else None,
                "duration_min": round(dur_s / 60, 2) if dur_s is not None else None,
            }
        )
    return results


def get_route(origin: tuple, destination: tuple) -> dict:
    """origin/destination: (lon, lat). Returns the road path to draw on a map:
    {"geometry": GeoJSON LineString, "distance_km", "duration_min", "source": "mapbox"|"estimate"}"""
    token = os.getenv("MAPBOX_ACCESS_TOKEN")
    if token:
        coords = f"{_fmt(*origin)};{_fmt(*destination)}"
        try:
            resp = requests.get(
                f"{MAPBOX_DIRECTIONS_URL}/{coords}",
                params={"geometries": "geojson", "overview": "full", "access_token": token},
                timeout=10,
            )
            if not resp.ok:
                log.warning("Mapbox directions %s: %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
            route = resp.json()["routes"][0]
            return {
                "geometry": route["geometry"],
                "distance_km": round(route["distance"] / 1000, 2),
                "duration_min": round(route["duration"] / 60, 2),
                "source": "mapbox",
            }
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            log.warning("Mapbox directions failed (%s) - using straight line", type(exc).__name__)
    km = _haversine_km(origin[1], origin[0], destination[1], destination[0]) * 1.3
    return {
        "geometry": {"type": "LineString", "coordinates": [list(origin), list(destination)]},
        "distance_km": round(km, 2),
        "duration_min": round(km / 25 * 60, 2),
        "source": "estimate",
    }