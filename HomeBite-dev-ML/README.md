# Delivery Dispatch System — Step-by-Step

FastAPI + Redis + Postgres + Mapbox. An order comes in, the system finds the best nearby driver,
locks them so nobody else can take them, and waits for accept / reject / timeout.

## What each file does (and the order to read them)

| # | File | Job |
|---|------|-----|
| 1 | `models.py` | Database tables: Driver, Restaurant, Order |
| 2 | `database.py` | Postgres/SQLite connection, session helper, startup migration |
| 3 | `redis_client.py` | Live driver locations (GEO) + atomic driver locks |
| 4 | `mapbox_client.py` | Real road distance/ETA for a shortlist of drivers |
| 5 | `dispatch.py` | The brain: shortlist -> Mapbox -> score -> lock |
| 6 | `routers/drivers.py`, `restaurants.py`, `orders.py` | The HTTP endpoints |
| 7 | `scheduler.py` | Background job: driver timeouts + retrying orders with no driver |
| 8 | `main.py` | Wires it all together |
| 9 | `simulate_driver.py`, `static/track.html` | Fake driver app + live tracking map |

---


## Important: upgrading an existing PostgreSQL database

`SQLModel.metadata.create_all()` creates missing tables but does not add columns to an existing table. This project now runs a small, idempotent PostgreSQL migration during startup to add the newer order-delivery fields (`customer_name`, `customer_phone`, `customer_address`, `customer_note`, `items`, `payment_type`, `amount_to_collect`, `tip`, and `delivery_code`). Existing order rows are retained. A standalone copy is also provided as `migrate_order_fields.sql`.

If you prefer to run the SQL manually, open pgAdmin Query Tool for the database configured in `.env` and run `migrate_order_fields.sql`. Do not drop the `order` table to fix a missing-column error.

## Windows quick start

1. Install Python 3.11+ and Docker Desktop (if using PostgreSQL/Redis).
2. Open PowerShell in this project folder.
3. Create and activate a virtual environment:

   ```powershell
   py -m venv venv
   .\venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and fill in `DATABASE_URL` and `REDIS_URL`. Leave `DATABASE_URL` blank to use SQLite; Redis is required for live driver matching.
5. For Docker services, run `docker compose up -d`. The compose file exposes PostgreSQL on port `5432` and Redis on `6379`.
6. Start the API: `python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload`.
7. Open `http://127.0.0.1:8000/docs`.

Never share or commit `.env`. The ZIP intentionally excludes local `.env`, virtual environments, Git internals, cache files, and the local SQLite database.

## Step 1 — Set up Python

```bash
cd HomeBite   # your project folder
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Step 2 — Start Postgres and Redis

Easiest: Docker.

```bash
docker compose up -d
```

No Docker? Install them directly (Ubuntu):

```bash
sudo apt install postgresql redis-server
sudo -u postgres createdb dispatch_db
redis-server --daemonize yes
```
(then set `DATABASE_URL` in `.env` to match your own Postgres user/password)

## Step 3 — Configure secrets

```bash
cp .env.example .env
```
Paste your Mapbox token into `.env` (free at mapbox.com -> Tokens).
**No token yet?** Leave it empty. The app falls back to straight-line estimates so you can test everything.

## Step 4 — Run the tests (no Postgres/Redis needed)

```bash
python -m pytest tests -q
```
You should see `12 passed`. These cover: nearest driver wins, no double assignment, reject -> reassign,
timeout -> reassign, retry of pending orders, delivery-code check, offline/online drivers, live tracking and Mapbox response parsing.

## Step 5 — Start the server

```bash
uvicorn main:app --reload
```
Open http://localhost:8000/docs — every endpoint can be tried from the browser.
All POST endpoints take a **JSON body** (not query parameters).

## Step 6 — Try the full flow

```bash
# 1. create a restaurant
curl -X POST http://localhost:8000/restaurants/ -H "Content-Type: application/json" \
  -d '{"name":"Spice Hub","lat":19.9975,"lon":73.7898}'

# 2. create two drivers (they appear in Redis immediately)
curl -X POST http://localhost:8000/drivers/ -H "Content-Type: application/json" \
  -d '{"name":"Ravi","lat":19.9990,"lon":73.7900}'
curl -X POST http://localhost:8000/drivers/ -H "Content-Type: application/json" \
  -d '{"name":"Amit","lat":20.0010,"lon":73.7850}'

# 3. simulate a GPS ping from driver 1
curl -X POST http://localhost:8000/drivers/location -H "Content-Type: application/json" \
  -d '{"driver_id":1,"lat":19.9985,"lon":73.7895}'

# 4. place an order -> nearest/best driver is assigned
curl -X POST http://localhost:8000/orders/ -H "Content-Type: application/json" \
  -d '{"restaurant_id":1,"customer_lat":20.01,"customer_lon":73.80,"customer_name":"Asha"}'

# 5. the driver app polls for its offer (dev response includes the delivery code)
curl "http://localhost:8000/drivers/offer?driver_id=1"

# 6. driver responds (accept=false hands it to the next best driver)
curl -X POST http://localhost:8000/orders/respond -H "Content-Type: application/json" \
  -d '{"order_id":1,"driver_id":1,"accept":true}'

# 7. driver collects the food, then hands it over using the customer's 4-digit code
curl -X POST http://localhost:8000/orders/pickup -H "Content-Type: application/json" \
  -d '{"order_id":1,"driver_id":1}'
curl -X POST http://localhost:8000/orders/delivered -H "Content-Type: application/json" \
  -d '{"order_id":1,"driver_id":1,"delivery_code":"1234"}'

# check an order any time
curl "http://localhost:8000/orders/1"
```

Expected response for step 4:
```json
{"order_id": 1, "status": "assigned", "assigned_driver": 1, "distance_km": 0.3, "eta_minutes": 0.9}
```

Other driver endpoints: `POST /drivers/online` and `POST /drivers/offline` (body `{"driver_id": 1}`).
An order can only be marked delivered after pickup, and only with the correct delivery code.

---

## How dispatch works (Step A-D inside `dispatch.py`)

- **A. Redis shortlist** – `GEOSEARCH` returns drivers within 3 km, nearest first. Busy/unavailable/rejected drivers are skipped. Cap: 15.
- **B. One Mapbox call** – real road distance + ETA for the whole shortlist at once.
- **C. Score** – `(1 / (ETA + 1)) * 0.7 + (rating / 5) * 0.3`. Tune the weights to taste.
- **D. Lock with fallback** – `SADD` on `drivers:busy` is atomic: only one order can win a given driver. If the top pick is taken, try the next.

## What I changed from the PDF's code

1. **Reject now really reassigns** (the PDF left a comment saying "re-run find_best_driver here"). Rejected drivers are remembered per order so they aren't offered it again.
2. **Timeout job added** (`scheduler.py`): assigned orders with no answer in 20s are released and reassigned. It also retries pending orders every 5s.
3. **Per-order lock** so the background job and an API request can never assign the same order twice.
4. Added endpoints to **create restaurants/drivers** and mark an order **delivered** (needed for a real test, and frees the driver).
5. `lifespan` instead of the deprecated `@app.on_event("startup")`; timezone-safe timestamps; SQLite fallback; Mapbox timeout + fallback if the API is down.

## Known limits (from the PDF's "scale" table)

- `delivery_code` is returned by `GET /drivers/offer` for development only: remove it before production.
- Driver locations in Redis never expire — a driver whose app crashes stays "there" until they ping again.
- Greedy one-order-at-a-time assignment; for heavy load look at batch matching (Hungarian algorithm).
- Use WebSockets/push to notify drivers instead of polling.
- Live traffic: change the Mapbox profile to `driving-traffic` (limit drops to 10 coordinates).


---

## Live tracking (restaurant pickup -> customer delivery)

Endpoints: `POST /orders/` (JSON body incl. `customer_lat`/`customer_lon`), `POST /orders/pickup`,
`GET /orders/{id}/tracking` (poll every 3s), `GET /orders/{id}/route` (Mapbox road path, cached 10s).
Status flow: pending -> assigned -> accepted -> picked_up -> delivered.

1. Put a **public** token (`pk.*`) in `.env` as `MAPBOX_PUBLIC_TOKEN` (the secret one stays in `MAPBOX_ACCESS_TOKEN`).
2. Create restaurant, driver and an order with customer coordinates, then open `http://localhost:8000/track?order=1`.
3. No phone handy? Right after placing the order run `python simulate_driver.py 1 1` (order id, driver id) and watch the red marker drive both legs. The order must still be in the `assigned` state, since the script reads the offer to get the delivery code.

The blue dashed line is the planned road route; the grey line is where the driver actually went (breadcrumbs stored in Redis).
In your real driver app, send GPS to `POST /drivers/location` (body `driver_id`, `lat`, `lon`) every 3-5s while an order is accepted/picked_up.

---

## ML models (recommendations)

All ML code lives under `ml/`, one folder per model, so the dispatch code is untouched.

    ml/recommendation/
        recommender.py      the model (SVD collaborative filtering + content + popularity)
        data/               users.csv, tiffins.csv, providers.csv, interactions.csv
        tools/              generate_synthetic_data.py, evaluate.py

Endpoints (`routers/recommendations.py`):

| Method | Path | Notes |
|---|---|---|
| POST | `/recommend` | header `X-Reco-Api-Key`; one ranked list. Body: `user_id`, `profile`, `top_k`, `meal_type`, `max_price`, `lat`, `lng`, `radius_km`, `health_conditions`, `observe_fasting`, `on_date` |
| POST | `/recommend/feed` | same body; returns titled rows: festival, health, for_you, popular_nearby (no dish repeats) |
| GET | `/recommend/options` | supported health conditions + festival calendar |
| GET | `/recommend/health` | open, shows model size |
| POST | `/recommend/retrain` | header `X-Reco-Api-Key`; reloads CSVs and retrains |

Setup: `pip install -r requirements.txt`, set `RECO_API_KEY` in `.env`.
Offline check: `PYTHONPATH=. python ml/recommendation/tools/evaluate.py`.
Tests: `pytest` (12 dispatch tests + 6 recommendation tests).

To add another model later (ETA prediction, demand forecasting...): create `ml/<name>/`, add `routers/<name>.py`,
and `app.include_router(...)` in `main.py`.

### Festivals and health rules

* `ml/recommendation/data/festivals.csv` -- name, tag, start, end, boost, fasting. Edit it every year (festival dates move);
  dishes carry `festival_tags` in `tiffins.csv`. Dates in the sample file are from public calendars -- verify before launch.
* `ml/recommendation/rules.py` -- `HEALTH_RULES` maps a condition to hard exclusions and soft penalties over the
  `sugar_level / sodium_level / carb_level / fat_level` columns. Sample nutrition levels are heuristics: replace them with
  values entered by providers or checked by a dietitian. Output is dietary suggestion, not medical advice.
* Health flags are received per request and never stored or logged by this service.
