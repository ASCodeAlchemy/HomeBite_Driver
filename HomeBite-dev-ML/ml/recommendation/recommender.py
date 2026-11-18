"""Hybrid food recommender: SVD collaborative filtering + content-based taste vector.

Works for three kinds of callers:
  * known user with order history   -> CF + content blend
  * known/new user with a profile   -> content (profile) + popularity
  * anonymous                       -> popularity + quality
Diet is always a hard filter.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd

from .rules import (SUPPORTED_CONDITIONS, active_festivals, apply_health, festival_boost,
                    health_labels, load_festivals)

CUISINES = ["North Indian", "South Indian", "Maharashtrian", "Gujarati", "Punjabi", "Jain", "Bengali"]
MEALS = ["breakfast", "lunch", "dinner"]
K_FACTORS = 16
MIN_HISTORY_FOR_CF = 5

MEAL_BOOST = 0.15      # soft nudge toward the user's preferred meal
BUDGET_PENALTY = 0.5   # score lost per 100% over budget


def _minmax(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x
    lo, hi = float(x.min()), float(x.max())
    return np.zeros_like(x) if hi - lo < 1e-9 else (x - lo) / (hi - lo)


def _km(lat1, lng1, lat2, lng2):
    return np.hypot((lat1 - lat2) * 111.0, (lng1 - lng2) * 105.0)


def _uid(x):
    """User ids are strings (hashed emails from Spring); synthetic ints are normalised too."""
    return None if x is None or x == "" else str(x)


def _nz(v, cast=None):
    """pandas NaN / missing -> None, otherwise optionally cast."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return cast(v) if cast else v


@dataclass
class Profile:
    diet_type: str | None = None          # veg | non_veg | eggetarian | jain
    spice_pref: int | None = None         # 1..5
    budget_per_meal: float | None = None
    fav_cuisines: list[str] | None = None
    preferred_meal: str | None = None


class Recommender:
    def __init__(self, data_dir: str | Path, interactions: pd.DataFrame | None = None):
        d = Path(data_dir)
        self.users = pd.read_csv(d / "users.csv", dtype={"user_id": str}).set_index("user_id", drop=False)
        self.tiffins = pd.read_csv(d / "tiffins.csv").reset_index(drop=True)
        self.providers = pd.read_csv(d / "providers.csv").set_index("provider_id")
        raw = interactions if interactions is not None else pd.read_csv(d / "interactions.csv", dtype={"user_id": str})
        self.interactions = raw.assign(user_id=raw["user_id"].astype(str))
        self.festivals = load_festivals(d / "festivals.csv")
        self.fit()

    # ------------------------------------------------------------------ training
    def fit(self) -> None:
        t = self.tiffins
        self.item_ids = t["tiffin_id"].to_numpy()
        self.item_idx = {tid: i for i, tid in enumerate(self.item_ids)}

        # item feature matrix
        cu = np.stack([(t.cuisine == c).to_numpy(float) for c in CUISINES], 1)
        me = np.stack([(t.meal_type == m).to_numpy(float) for m in MEALS], 1)
        self.price_max = float(t.price.max())
        feats = np.hstack([
            cu, me,
            t.veg.to_numpy(float)[:, None],
            ((t.spice_level.to_numpy(float) - 1) / 4)[:, None],
            (t.price.to_numpy(float) / self.price_max)[:, None],
        ])
        self.item_feats = feats
        self.item_unit = feats / (np.linalg.norm(feats, axis=1, keepdims=True) + 1e-9)

        # user-item matrix (log-count, plus a small bonus for good ratings)
        inter = self.interactions
        self.user_ids = np.array(sorted(inter.user_id.unique()))
        self.user_idx = {u: i for i, u in enumerate(self.user_ids)}
        R = np.zeros((len(self.user_ids), len(self.item_ids)))
        g = inter.groupby(["user_id", "tiffin_id"]).agg(n=("order_id", "size"), r=("rating", "mean")).reset_index()
        for row in g.itertuples():
            if row.tiffin_id not in self.item_idx:
                continue
            bonus = 0.0 if math.isnan(row.r) else (row.r - 3.5) * 0.3
            R[self.user_idx[row.user_id], self.item_idx[row.tiffin_id]] = max(math.log1p(row.n) + bonus, 0.0)
        self.R = R
        k = min(K_FACTORS, min(R.shape) - 1)
        U, S, Vt = np.linalg.svd(R, full_matrices=False)
        self.cf_pred = (U[:, :k] * S[:k]) @ Vt[:k]

        # popularity (orders + quality)
        pop = np.zeros(len(self.item_ids))
        for tid, n in inter.tiffin_id.value_counts().items():
            if tid in self.item_idx:
                pop[self.item_idx[tid]] = n
        self.popularity = _minmax(np.log1p(pop)) * 0.8 + _minmax(t.provider_quality.to_numpy(float)) * 0.2
        self.history_counts = inter.groupby("user_id").size().to_dict()

    # ------------------------------------------------------------------ helpers
    def _profile_for(self, user_id, override: Profile | None) -> Profile:
        p = Profile()
        if user_id is not None and user_id in self.users.index:
            u = self.users.loc[user_id]
            fav = _nz(u.get("fav_cuisines"))
            p = Profile(
                diet_type=_nz(u.get("diet_type")),
                spice_pref=_nz(u.get("spice_pref"), int),
                budget_per_meal=_nz(u.get("budget_per_meal"), float),
                fav_cuisines=str(fav).split("|") if fav else None,
                preferred_meal=_nz(u.get("preferred_meal")),
            )
        if override:
            for f in ("diet_type", "spice_pref", "budget_per_meal", "fav_cuisines", "preferred_meal"):
                v = getattr(override, f)
                if v is not None:
                    setattr(p, f, v)
        return p

    def _profile_vector(self, p: Profile) -> np.ndarray | None:
        if not any([p.fav_cuisines, p.spice_pref, p.preferred_meal, p.budget_per_meal]):
            return None
        v = np.zeros(self.item_feats.shape[1])
        fav = set(p.fav_cuisines or [])
        for i, c in enumerate(CUISINES):
            v[i] = 1.0 if c in fav else 0.0
        if p.preferred_meal in MEALS:
            v[len(CUISINES) + MEALS.index(p.preferred_meal)] = 1.0
        v[-3] = 1.0 if p.diet_type in ("veg", "jain") else 0.5
        v[-2] = ((p.spice_pref or 3) - 1) / 4
        v[-1] = min((p.budget_per_meal or 100) / self.price_max, 1.0)
        return v / (np.linalg.norm(v) + 1e-9)

    def _diet_mask(self, diet: str | None) -> np.ndarray:
        t = self.tiffins
        if diet in ("veg", "jain"):
            m = t.veg.to_numpy(bool).copy()
            if diet == "jain":
                m &= (t.cuisine.isin(["Jain", "Gujarati"]) |
                      t.dish_name.str.contains("Khichdi|Dhokla|Curd")).to_numpy(bool)
            return m
        if diet == "eggetarian":
            return (t.veg | t.dish_name.str.contains("Egg")).to_numpy(bool).copy()
        return np.ones(len(t), bool)

    # ------------------------------------------------------------------ inference
    def recommend(self, user_id=None, profile: Profile | None = None, top_k: int = 10,
                  meal_type: str | None = None, max_price: float | None = None,
                  lat: float | None = None, lng: float | None = None,
                  radius_km: float | None = None, exclude_ids: set[int] | None = None,
                  health_conditions: list[str] | None = None, today: date | None = None,
                  observe_fasting: bool = False, festival_only: bool = False,
                  healthy_only: bool = False, popular_only: bool = False) -> dict:
        user_id = _uid(user_id)
        t = self.tiffins
        today = today or date.today()
        prof = self._profile_for(user_id, profile)
        n_hist = self.history_counts.get(user_id, 0)
        has_history = n_hist >= MIN_HISTORY_FOR_CF and user_id in self.user_idx and not popular_only

        # ---- learned part: content + collaborative + popularity
        if has_history:
            w = self.R[self.user_idx[user_id]]
            taste = (w[:, None] * self.item_feats).sum(0)
            taste = taste / (np.linalg.norm(taste) + 1e-9)
        else:
            taste = None if popular_only else self._profile_vector(prof)
        content = _minmax(self.item_unit @ taste) if taste is not None else np.zeros(len(t))

        cf = np.zeros(len(t))
        if has_history:
            cf = _minmax(self.cf_pred[self.user_idx[user_id]])
            score = 0.55 * cf + 0.30 * content + 0.15 * self.popularity
            mode = "hybrid"
        elif taste is not None:
            score = 0.70 * content + 0.30 * self.popularity
            mode = "profile"
        else:
            score = self.popularity.copy()
            mode = "popular"

        if prof.budget_per_meal:
            over = np.maximum(t.price.to_numpy(float) - prof.budget_per_meal, 0) / prof.budget_per_meal
            score = score - BUDGET_PENALTY * over

        dist = np.full(len(t), np.nan)
        if lat is not None and lng is not None:
            pl = self.providers.reindex(t.provider_id.to_numpy())   # unknown provider -> NaN, never a KeyError
            dist = _km(lat, lng, pl.lat.to_numpy(float), pl.lng.to_numpy(float))
            score = score - 0.03 * np.nan_to_num(dist, nan=0.0)      # NaN distance must not poison the sort

        # ---- rule layer: festivals
        active = active_festivals(self.festivals, today)
        f_boost, f_name = festival_boost(t, active)
        score = score + f_boost

        # ---- soft preference: preferred meal (a boost, never a filter)
        meal_match = np.zeros(len(t))
        if prof.preferred_meal in MEALS:
            meal_match = (t.meal_type.to_numpy() == prof.preferred_meal).astype(float)
            score = score + MEAL_BOOST * meal_match

        # ---- rule layer: health
        h_exclude, h_penalty, applied, ignored = apply_health(t, health_conditions)
        score = score - h_penalty

        # ---- hard filters
        mask = self._diet_mask(prof.diet_type) & ~h_exclude
        fasting_on = bool(observe_fasting and not active.empty and active.fasting.any())
        if fasting_on:
            mask &= t.fasting_friendly.to_numpy(bool)
        if festival_only:
            mask &= f_boost > 0
        if healthy_only:
            mask &= h_penalty < 1e-9
        if meal_type:
            mask &= (t.meal_type == meal_type).to_numpy(bool)
        if max_price:
            mask &= (t.price <= max_price).to_numpy(bool)
        if radius_km is not None and not np.isnan(dist).all():
            mask &= dist <= radius_km
        if exclude_ids:
            mask &= ~t.tiffin_id.isin(exclude_ids).to_numpy(bool)

        score = np.where(mask, score, -np.inf)
        order = np.argsort(-score)[:top_k]
        fav = set(prof.fav_cuisines or [])
        items = []
        for i in order:
            if not np.isfinite(score[i]):
                break
            r = t.iloc[i]
            reasons = []
            if f_name[i]:
                reasons.append(f"{f_name[i]} special")
            reasons += health_labels(r, applied)
            if meal_match[i] and prof.preferred_meal:
                reasons.append(f"Good for {prof.preferred_meal}")
            if has_history and cf[i] >= 0.7:
                reasons.append("Because of your past orders")
            elif r.cuisine in fav:
                reasons.append(f"You like {r.cuisine}")
            if not np.isnan(dist[i]) and dist[i] <= 3:
                reasons.append("Near you")
            if not reasons and self.popularity[i] >= 0.75:
                reasons.append("Popular right now")
            items.append({
                "tiffin_id": int(r.tiffin_id), "provider_id": int(r.provider_id),
                "dish_name": r.dish_name, "cuisine": r.cuisine, "veg": bool(r.veg),
                "price": float(r.price), "meal_type": r.meal_type,
                "distance_km": None if np.isnan(dist[i]) else round(float(dist[i]), 2),
                "score": round(float(score[i]), 4),
                "reasons": reasons[:3],
            })
        return {
            "user_id": user_id, "mode": mode, "items": items,
            "context": {
                "date": today.isoformat(),
                "active_festivals": active.name.tolist(),
                "fasting_mode": fasting_on,
                "health_applied": applied,
                "health_ignored": ignored,
                "notice": "Dietary suggestions only, not medical advice." if applied else None,
            },
        }

    def feed(self, user_id=None, profile: Profile | None = None, top_k: int = 10, section_size: int = 6, **kw) -> dict:
        """Explore/dashboard feed: several titled rows, no dish repeated across rows."""
        sections, seen = [], set(kw.pop("exclude_ids", None) or [])

        def add(key, title, **extra):
            res = self.recommend(user_id=user_id, profile=profile, top_k=extra.pop("k", section_size),
                                 exclude_ids=set(seen), **{**kw, **extra})
            if res["items"]:
                sections.append({"key": key, "title": title, "items": res["items"]})
                seen.update(i["tiffin_id"] for i in res["items"])
            return res

        first = self.recommend(user_id=user_id, profile=profile, top_k=1, exclude_ids=set(seen), **kw)
        ctx = first["context"]
        if ctx["active_festivals"]:
            add("festival", f"{ctx['active_festivals'][0]} specials", festival_only=True)
        if ctx["health_applied"]:
            add("health", "Picked for your health goals", healthy_only=True)
        add("for_you", "Recommended for you", k=top_k)
        if kw.get("lat") is not None:
            add("popular_nearby", "Popular near you", popular_only=True)
        return {"user_id": user_id, "mode": first["mode"], "context": ctx, "sections": sections}