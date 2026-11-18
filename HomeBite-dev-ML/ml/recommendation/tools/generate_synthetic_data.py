"""Synthetic HomeBite data for a food recommender.
Outputs users.csv, tiffins.csv, interactions.csv in the output dir.
Usage: python generate_synthetic_data.py [out_dir] [n_users]
"""
import sys, uuid
from pathlib import Path
import numpy as np
import pandas as pd

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
N_USERS = int(sys.argv[2]) if len(sys.argv) > 2 else 200
rng = np.random.default_rng(42)
OUT.mkdir(parents=True, exist_ok=True)

# Pune areas with approximate centre coordinates
AREAS = {
    "Kothrud": (18.5074, 73.8077), "Baner": (18.5590, 73.7868),
    "Hinjewadi": (18.5913, 73.7389), "Viman Nagar": (18.5679, 73.9143),
    "Hadapsar": (18.5089, 73.9260), "Shivajinagar": (18.5308, 73.8474),
    "Wakad": (18.5987, 73.7603), "Kharadi": (18.5515, 73.9400),
    "Karve Nagar": (18.4900, 73.8200), "Pimple Saudagar": (18.6020, 73.7960),
}
CUISINES = ["North Indian", "South Indian", "Maharashtrian", "Gujarati", "Punjabi", "Jain", "Bengali"]
MEAL_TYPES = ["breakfast", "lunch", "dinner"]

# (dish, cuisine, veg, spice 1-5, base price, meal_type, calories)
DISHES = [
    ("Poha", "Maharashtrian", True, 2, 60, "breakfast", 250),
    ("Misal Pav", "Maharashtrian", True, 5, 90, "breakfast", 450),
    ("Sabudana Khichdi", "Maharashtrian", True, 1, 70, "breakfast", 350),
    ("Idli Sambar", "South Indian", True, 2, 70, "breakfast", 300),
    ("Masala Dosa", "South Indian", True, 3, 90, "breakfast", 400),
    ("Upma", "South Indian", True, 2, 60, "breakfast", 280),
    ("Aloo Paratha", "Punjabi", True, 3, 80, "breakfast", 420),
    ("Dhokla", "Gujarati", True, 1, 70, "breakfast", 200),
    ("Thepla Combo", "Gujarati", True, 2, 90, "lunch", 450),
    ("Gujarati Thali", "Gujarati", True, 2, 140, "lunch", 700),
    ("Maharashtrian Thali", "Maharashtrian", True, 4, 130, "lunch", 700),
    ("Pithla Bhakri", "Maharashtrian", True, 3, 100, "lunch", 550),
    ("Varan Bhaat", "Maharashtrian", True, 1, 80, "lunch", 480),
    ("Rajma Chawal", "Punjabi", True, 3, 100, "lunch", 600),
    ("Chole Bhature", "Punjabi", True, 4, 110, "lunch", 750),
    ("Paneer Butter Masala Meal", "North Indian", True, 3, 150, "lunch", 700),
    ("Dal Tadka Rice", "North Indian", True, 3, 90, "dinner", 550),
    ("Veg Biryani", "North Indian", True, 3, 120, "dinner", 600),
    ("Jain Thali", "Jain", True, 2, 140, "lunch", 620),
    ("Jain Khichdi", "Jain", True, 1, 90, "dinner", 450),
    ("Curd Rice", "South Indian", True, 1, 70, "dinner", 350),
    ("Sambar Rice", "South Indian", True, 3, 90, "lunch", 500),
    ("Lemon Rice Combo", "South Indian", True, 2, 80, "lunch", 450),
    ("Chicken Curry Meal", "North Indian", False, 4, 160, "lunch", 750),
    ("Butter Chicken Meal", "North Indian", False, 3, 180, "dinner", 800),
    ("Egg Curry Meal", "North Indian", False, 3, 120, "dinner", 600),
    ("Chicken Biryani", "North Indian", False, 4, 170, "dinner", 850),
    ("Mutton Thali", "Maharashtrian", False, 5, 220, "dinner", 900),
    ("Kolhapuri Chicken", "Maharashtrian", False, 5, 190, "dinner", 800),
    ("Fish Curry Rice", "Bengali", False, 3, 170, "lunch", 700),
    ("Egg Bhurji Pav", "Maharashtrian", False, 3, 80, "breakfast", 400),
    ("Machher Jhol Meal", "Bengali", False, 3, 180, "lunch", 700),
    ("Veg Khichuri", "Bengali", True, 2, 100, "dinner", 520),
    ("Luchi Aloo Dum", "Bengali", True, 2, 90, "breakfast", 480),
    ("Kadhi Khichdi", "Gujarati", True, 2, 100, "dinner", 520),
    ("Undhiyu Meal", "Gujarati", True, 3, 130, "lunch", 650),
    ("Palak Paneer Meal", "North Indian", True, 2, 140, "dinner", 620),
    ("Mix Veg Roti Meal", "North Indian", True, 2, 100, "dinner", 520),
    ("Veg Pulao", "North Indian", True, 2, 90, "dinner", 480),
    ("Sprouts Salad Bowl", "Maharashtrian", True, 1, 80, "lunch", 280),
]


# Festival / seasonal dishes: (dish, cuisine, veg, spice, price, meal_type, calories, festival_tags, fasting_friendly)
FESTIVAL_DISHES = [
    ("Vrat Thali (Sabudana + Kuttu)", "Maharashtrian", True, 1, 130, "lunch", 600, "navratri", True),
    ("Sabudana Vada Plate", "Maharashtrian", True, 2, 80, "breakfast", 420, "navratri", True),
    ("Kuttu Puri Aloo", "North Indian", True, 2, 90, "lunch", 520, "navratri", True),
    ("Samak Rice Khichdi", "North Indian", True, 1, 85, "dinner", 380, "navratri", True),
    ("Rajgira Paratha", "Punjabi", True, 1, 80, "breakfast", 380, "navratri", True),
    ("Mithai Box (Kaju Katli)", "North Indian", True, 1, 250, "dinner", 700, "diwali", False),
    ("Besan Ladoo Box", "North Indian", True, 1, 180, "dinner", 650, "diwali", False),
    ("Diwali Faral Combo", "Maharashtrian", True, 3, 160, "breakfast", 600, "diwali", False),
    ("Gulab Jamun Box", "North Indian", True, 1, 140, "dinner", 600, "diwali", False),
    ("Puran Poli", "Maharashtrian", True, 1, 100, "lunch", 480, "gudi_padwa|diwali", False),
    ("Til Gul Ladoo", "Maharashtrian", True, 1, 90, "dinner", 400, "makar_sankranti", False),
    ("Bajra Khichdi Thali", "Gujarati", True, 2, 120, "lunch", 560, "makar_sankranti", False),
    ("Gujiya Box", "North Indian", True, 1, 120, "dinner", 520, "holi", False),
    ("Thandai Meal Combo", "North Indian", True, 1, 130, "dinner", 480, "holi", False),
    ("Modak Plate", "Maharashtrian", True, 1, 110, "dinner", 450, "ganesh_chaturthi", False),
]

SWEETS = ("Mithai", "Ladoo", "Jamun", "Poli", "Gujiya", "Modak", "Thandai")
FRIED = ("Bhature", "Misal", "Paratha", "Puri", "Vada", "Faral", "Luchi", "Thepla")
HEAVY_FAT = ("Butter", "Mutton", "Kolhapuri", "Paneer", "Bhature", "Misal")
HIGH_SODIUM = ("Misal", "Chole", "Butter", "Mutton", "Kolhapuri", "Biryani", "Faral", "Chicken Curry")
HIGH_CARB = ("Biryani", "Pulao", "Bhature", "Puri", "Paratha", "Lemon Rice", "Khichuri", "Thali", "Mithai", "Ladoo", "Poli", "Jamun")
LIGHT = ("Idli", "Dhokla", "Salad", "Curd Rice", "Upma", "Varan", "Khichdi", "Sprouts")


def nutrition(dish):
    """Heuristic low/medium/high tags. REPLACE with real values entered by providers / a dietitian."""
    has = lambda keys: any(k.lower() in dish.lower() for k in keys)
    sugar = "high" if has(SWEETS) else "low"
    fat = "high" if has(HEAVY_FAT + SWEETS + FRIED) else ("low" if has(LIGHT) else "medium")
    sodium = "high" if has(HIGH_SODIUM) else ("low" if has(LIGHT + SWEETS) else "medium")
    carb = "high" if has(HIGH_CARB) else ("low" if has(("Salad", "Egg Curry", "Chicken Curry", "Butter Chicken")) else "medium")
    return sugar, sodium, carb, fat

# ---------- providers ----------
n_prov = 12
prov_areas = rng.choice(list(AREAS), n_prov)
providers = []
for i in range(n_prov):
    lat, lng = AREAS[prov_areas[i]]
    providers.append(dict(
        provider_id=i + 1, area=prov_areas[i],
        lat=lat + rng.normal(0, 0.004), lng=lng + rng.normal(0, 0.004),
        quality=float(np.clip(rng.normal(4.1, 0.35), 3.0, 5.0)),
        price_factor=float(rng.uniform(0.9, 1.25)),
    ))

# ---------- tiffins ----------
rows, tid = [], 1
for p in providers:
    picks = [DISHES[d] + ("", False) for d in rng.choice(len(DISHES), size=int(rng.integers(6, 10)), replace=False)]
    picks += [FESTIVAL_DISHES[d] for d in rng.choice(len(FESTIVAL_DISHES), size=int(rng.integers(3, 6)), replace=False)]
    for name, cuisine, veg, spice, price, meal, cal, ftags, vrat in picks:
        sugar, sodium, carb, fat = nutrition(name)
        rows.append(dict(
            tiffin_id=tid, provider_id=p["provider_id"], dish_name=name, cuisine=cuisine,
            veg=veg, spice_level=int(np.clip(spice + rng.integers(-1, 2), 1, 5)),
            price=int(round(price * p["price_factor"] / 5) * 5), meal_type=meal,
            calories=int(cal + rng.integers(-40, 41)),
            provider_quality=round(p["quality"], 2),
            subscription_available=bool(rng.random() < 0.6),
            sugar_level=sugar, sodium_level=sodium, carb_level=carb, fat_level=fat,
            fasting_friendly=bool(vrat), festival_tags=ftags,
        ))
        tid += 1
tiffins = pd.DataFrame(rows)

# ---------- users ----------
users = []
for u in range(1, N_USERS + 1):
    area = rng.choice(list(AREAS))
    lat, lng = AREAS[area]
    diet = rng.choice(["veg", "non_veg", "eggetarian", "jain"], p=[0.42, 0.38, 0.10, 0.10])
    age_group = rng.choice(["18-24", "25-34", "35-44", "45+"], p=[0.32, 0.38, 0.20, 0.10])
    is_student = bool(age_group == "18-24" and rng.random() < 0.7)
    budget = int(np.clip(rng.normal(80 if is_student else 130, 30), 50, 250) // 5 * 5)
    fav = rng.choice(CUISINES, size=int(rng.integers(1, 4)), replace=False)
    users.append(dict(
        user_id=u, age_group=age_group, diet_type=diet, is_student=is_student,
        spice_pref=int(np.clip(rng.normal(3, 1.1), 1, 5)),
        budget_per_meal=budget, fav_cuisines="|".join(fav),
        preferred_meal=rng.choice(MEAL_TYPES, p=[0.2, 0.5, 0.3]),
        area=area, lat=round(lat + rng.normal(0, 0.006), 6), lng=round(lng + rng.normal(0, 0.006), 6),
        orders_per_week=int(np.clip(rng.poisson(3.5), 1, 12)),
        signup_date=(pd.Timestamp("2026-10-01") - pd.to_timedelta(rng.integers(30, 400), unit="D")).date(),
    ))
users = pd.DataFrame(users)

# ---------- interactions ----------
def km(lat1, lng1, lat2, lng2):
    return np.hypot((lat1 - lat2) * 111, (lng1 - lng2) * 105)

prov = pd.DataFrame(providers).set_index("provider_id")
ints = []
for _, u in users.iterrows():
    # which items are even acceptable
    ok = tiffins.copy()
    if u.diet_type in ("veg", "jain"):
        ok = ok[ok.veg]
    if u.diet_type == "jain":
        ok = ok[ok.cuisine.isin(["Jain", "Gujarati"]) | ok.dish_name.str.contains("Khichdi|Dhokla|Curd")]
    elif u.diet_type == "eggetarian":
        ok = ok[ok.veg | ok.dish_name.str.contains("Egg")]
    if ok.empty:
        continue
    dist = km(u.lat, u.lng, prov.loc[ok.provider_id, "lat"].values, prov.loc[ok.provider_id, "lng"].values)
    score = (
        1.6 * ok.cuisine.isin(u.fav_cuisines.split("|")).values
        - 0.5 * np.abs(ok.spice_level.values - u.spice_pref)
        - 0.02 * np.maximum(ok.price.values - u.budget_per_meal, 0)
        + 0.8 * (ok.meal_type.values == u.preferred_meal)
        + 0.6 * (ok.provider_quality.values - 4)
        - 0.25 * dist
    )
    p = np.exp(score - score.max()); p /= p.sum()
    n_orders = int(u.orders_per_week * rng.uniform(4, 10))
    picks = rng.choice(len(ok), size=n_orders, p=p)
    for k in picks:
        it = ok.iloc[k]
        affinity = score[k]
        rating = np.clip(round(3.7 + 0.35 * affinity + rng.normal(0, 0.6)), 1, 5)
        hour = {"breakfast": 8, "lunch": 13, "dinner": 20}[it.meal_type] + int(rng.integers(-1, 2))
        ts = pd.Timestamp("2026-10-01") - pd.to_timedelta(int(rng.integers(0, 90)), unit="D") \
            + pd.to_timedelta(hour, unit="h") + pd.to_timedelta(int(rng.integers(0, 60)), unit="m")
        ints.append(dict(
            order_id=str(uuid.UUID(int=int(rng.integers(0, 2**63)) << 64 | int(rng.integers(0, 2**63)))),
            user_id=int(u.user_id), tiffin_id=int(it.tiffin_id), provider_id=int(it.provider_id),
            order_time=ts, order_type="subscription" if it.subscription_available and rng.random() < 0.25 else "individual",
            quantity=int(rng.choice([1, 1, 1, 2])), rating=int(rating) if rng.random() < 0.7 else None,
        ))
interactions = pd.DataFrame(ints).sort_values("order_time")

users.to_csv(OUT / "users.csv", index=False)
tiffins.to_csv(OUT / "tiffins.csv", index=False)
interactions.to_csv(OUT / "interactions.csv", index=False)
pd.DataFrame(providers)[["provider_id","area","lat","lng"]].round(6).to_csv(OUT / "providers.csv", index=False)
print(f"users={len(users)} tiffins={len(tiffins)} interactions={len(interactions)}")
