"""Rule layer that sits on top of the learned model: health-aware filtering and festival boosts.

These are *dietary suggestions*, not medical advice. Nutrition levels (low/medium/high) on each tiffin
should come from providers / a dietitian -- the sample data uses heuristics.
"""
from __future__ import annotations
from datetime import date
import numpy as np
import pandas as pd

# condition -> how it changes the feed
#   exclude : hard-remove items whose column has one of these levels
#   penalty : soft score reduction by column/level
#   max_cal : soft penalty above this calorie count
HEALTH_RULES: dict[str, dict] = {
    "diabetes": {
        "label": "Lower sugar",
        "exclude": {"sugar_level": ["high"]},
        "penalty": {"carb_level": {"high": 0.30, "medium": 0.08}},
    },
    "hypertension": {
        "label": "Lower sodium",
        "exclude": {"sodium_level": ["high"]},
        "penalty": {"sodium_level": {"medium": 0.10}},
    },
    "heart": {
        "label": "Lighter on fat",
        "exclude": {},
        "penalty": {"fat_level": {"high": 0.40, "medium": 0.08}, "sodium_level": {"high": 0.20}},
    },
    "high_cholesterol": {
        "label": "Lighter on fat",
        "exclude": {},
        "penalty": {"fat_level": {"high": 0.40, "medium": 0.08}},
    },
    "weight_loss": {
        "label": "Lighter on calories",
        "exclude": {},
        "penalty": {"carb_level": {"high": 0.15}},
        "max_cal": 600,
    },
}
SUPPORTED_CONDITIONS = sorted(HEALTH_RULES)


def apply_health(tiffins: pd.DataFrame, conditions: list[str] | None):
    """Returns (exclude_mask, penalty_array, applied_conditions, ignored_conditions)."""
    n = len(tiffins)
    exclude = np.zeros(n, bool)
    penalty = np.zeros(n)
    applied, ignored = [], []
    for c in (conditions or []):
        rule = HEALTH_RULES.get(c)
        if not rule:
            ignored.append(c)
            continue
        applied.append(c)
        for col, levels in rule["exclude"].items():
            exclude |= tiffins[col].isin(levels).to_numpy(bool)
        for col, by_level in rule["penalty"].items():
            penalty += tiffins[col].map(by_level).fillna(0).to_numpy(float)
        if "max_cal" in rule:
            cal = np.nan_to_num(tiffins.calories.to_numpy(float), nan=0.0)   # unknown calories -> no penalty
            over = np.maximum(cal - rule["max_cal"], 0) / 400
            penalty += 0.30 * over
    return exclude, penalty, applied, ignored


def health_labels(row, conditions: list[str]) -> list[str]:
    """Human-friendly 'why' labels for an item that suits the user's conditions."""
    out = []
    for c in conditions:
        if c == "diabetes" and row.sugar_level == "low" and row.carb_level != "high":
            out.append("Lower sugar")
        elif c == "hypertension" and row.sodium_level == "low":
            out.append("Low sodium")
        elif c in ("heart", "high_cholesterol") and row.fat_level == "low":
            out.append("Light on fat")
        elif c == "weight_loss" and row.calories <= 500:
            out.append("Light on calories")
    return list(dict.fromkeys(out))


def load_festivals(path) -> pd.DataFrame:
    try:
        f = pd.read_csv(path, parse_dates=["start", "end"])
        f["fasting"] = f["fasting"].astype(str).str.lower().isin(("true", "1", "yes"))
        return f
    except FileNotFoundError:
        return pd.DataFrame(columns=["name", "tag", "start", "end", "boost", "fasting", "note"])


def active_festivals(festivals: pd.DataFrame, today: date) -> pd.DataFrame:
    d = pd.Timestamp(today)
    return festivals[(festivals.start <= d) & (festivals.end >= d)]


def festival_boost(tiffins: pd.DataFrame, active: pd.DataFrame):
    """Returns (boost_array, matched_festival_name_per_item)."""
    n = len(tiffins)
    boost = np.zeros(n)
    names: list[str | None] = [None] * n
    if active.empty:
        return boost, names
    item_tags = tiffins.festival_tags.fillna("").astype(str).str.split("|")
    for i, tags in enumerate(item_tags):
        best = 0.0
        for fest in active.itertuples():
            if fest.tag in tags and fest.boost > best:
                best, names[i] = float(fest.boost), fest.name
        boost[i] = best
    return boost, names
