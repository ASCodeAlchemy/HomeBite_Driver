# """Offline check: hold out each user's most recent 20% of orders, measure HitRate@10 / Recall@10
# against a most-popular baseline.  Run: python evaluate.py"""
# import numpy as np, pandas as pd
# from pathlib import Path
# from ml.recommendation.recommender import Recommender
#
# D = Path(__file__).resolve().parent.parent / "data"
# inter = pd.read_csv(D / "interactions.csv", parse_dates=["order_time"]).sort_values("order_time")
# train, test = [], []
# for u, g in inter.groupby("user_id"):
#     cut = int(len(g) * 0.8)
#     train.append(g.iloc[:cut]); test.append(g.iloc[cut:])
# train, test = pd.concat(train), pd.concat(test)
#
# reco = Recommender(D, interactions=train)
# truth = test.groupby("user_id").tiffin_id.apply(set).to_dict()
# popular = train.tiffin_id.value_counts().index.tolist()
# users = reco.users
#
# def run(fn, name):
#     hits, rec = [], []
#     for u, items in truth.items():
#         got = fn(u)
#         h = len(items & set(got))
#         hits.append(h > 0); rec.append(h / len(items))
#     print(f"{name:<18} HitRate@10={np.mean(hits):.3f}  Recall@10={np.mean(rec):.3f}")
#
# def pop_fn(u):  # popularity baseline, diet-filtered
#     mask = reco._diet_mask(users.loc[u].diet_type)
#     ok = set(reco.tiffins.tiffin_id[mask])
#     return [t for t in popular if t in ok][:10]
#
# run(pop_fn, "popularity")
# run(lambda u: [i["tiffin_id"] for i in reco.recommend(u, top_k=10)["items"]], "hybrid")


"""Offline check on any data folder (synthetic or a real snapshot).

Holds out each user's most recent 20% of orders and measures HitRate@10 and Recall@10 for:
  * popularity   : most-ordered dishes, diet-filtered (the baseline to beat)
  * hybrid       : the live model, as the app calls it (CF used only for users with enough history)

It reports the whole population and the CF-eligible subset separately. Only the CF-eligible subset
can show whether collaborative filtering helps, so the decision is made on that subset.

Usage (from the project root):
    python -m ml.recommendation.tools.evaluate                 # synthetic demo data
    python -m ml.recommendation.tools.evaluate path/to/snapshot
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ml.recommendation import DATA_DIR as SYNTHETIC_DIR
from ml.recommendation.recommender import MIN_HISTORY_FOR_CF, Recommender

MIN_CF_USERS_FOR_DECISION = 30
MARGIN = 0.01  # hybrid must beat popularity by at least this much on HitRate@10


def split(inter: pd.DataFrame):
    inter = inter.dropna(subset=["order_time"]).sort_values("order_time")
    train, test = [], []
    for _, g in inter.groupby("user_id"):
        cut = int(len(g) * 0.8)
        train.append(g.iloc[:cut])
        test.append(g.iloc[cut:])
    return pd.concat(train), pd.concat(test)


def score(truth: dict, recs_for, users) -> dict:
    hits, rec = [], []
    for u in users:
        items = truth[u]
        got = set(recs_for(u))
        h = len(items & got)
        hits.append(h > 0)
        rec.append(h / len(items))
    if not users:
        return {"n": 0, "hit": float("nan"), "recall": float("nan")}
    return {"n": len(users), "hit": float(np.mean(hits)), "recall": float(np.mean(rec))}


def main(data_dir: Path) -> None:
    inter = pd.read_csv(data_dir / "interactions.csv", dtype={"user_id": str, "order_id": str},
                        parse_dates=["order_time"])
    train, test = split(inter)
    reco = Recommender(data_dir, interactions=train)
    reco.use_cf = True                         # evaluate the CF path explicitly, whatever the env says
    truth = test.groupby("user_id")["tiffin_id"].apply(set).to_dict()
    popular = train["tiffin_id"].value_counts().index.tolist()

    def popularity(u):
        mask = reco._diet_mask(reco._profile_for(u, None).diet_type)
        ok = set(reco.tiffins["tiffin_id"][mask])
        return [t for t in popular if t in ok][:10]

    def hybrid(u):
        return [i["tiffin_id"] for i in reco.recommend(u, top_k=10)["items"]]

    everyone = sorted(truth)
    eligible = [u for u in everyone if reco.history_counts.get(u, 0) >= MIN_HISTORY_FOR_CF]

    print(f"data: {data_dir}")
    print(f"train orders {len(train)}, test orders {len(test)}, users in test {len(everyone)}, "
          f"CF-eligible users {len(eligible)}\n")
    results = {}
    for label, users in [("all users", everyone), ("CF-eligible", eligible)]:
        for name, fn in [("popularity", popularity), ("hybrid", hybrid)]:
            r = score(truth, fn, users)
            results[(label, name)] = r
            print(f"{label:<13} {name:<11} n={r['n']:<4} HitRate@10={r['hit']:.3f}  Recall@10={r['recall']:.3f}")

    print()
    if len(eligible) < MIN_CF_USERS_FOR_DECISION:
        print(f"Decision: too few CF-eligible users ({len(eligible)} < {MIN_CF_USERS_FOR_DECISION}). "
              "Keep RECO_USE_CF=0 (profile + popularity).")
        return
    h = results[("CF-eligible", "hybrid")]["hit"]
    p = results[("CF-eligible", "popularity")]["hit"]
    if h >= p + MARGIN:
        print(f"Decision: hybrid beats popularity by {h - p:+.3f}. Keep RECO_USE_CF=1.")
    else:
        print(f"Decision: hybrid does not beat popularity ({h - p:+.3f}). "
              "Set RECO_USE_CF=0 and rely on profile + popularity until more orders arrive.")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else SYNTHETIC_DIR)