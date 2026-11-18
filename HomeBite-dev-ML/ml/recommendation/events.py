import asyncio
import json
import logging
import os
import threading

from kafka import KafkaConsumer

from ml.recommendation.sync import sync_now

log = logging.getLogger("reco.events")

TOPICS = ["order-events", "reco.events"]
DEBOUNCE_SECONDS = int(os.getenv("RECO_SYNC_DEBOUNCE_SECONDS", "60"))

_timer_lock = threading.Lock()
_sync_lock = threading.Lock()
_timer: threading.Timer | None = None


def _run_sync() -> None:
    with _sync_lock:
        try:
            # sync_now() is async and reloads the recommender itself
            summary = asyncio.run(sync_now())
            log.info("incremental sync complete: %s", summary)
        except Exception:
            log.exception("incremental sync failed")


def _schedule_sync() -> None:
    global _timer
    with _timer_lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(DEBOUNCE_SECONDS, _run_sync)
        _timer.daemon = True
        _timer.start()


def _consume(bootstrap: str) -> None:
    while True:
        try:
            consumer = KafkaConsumer(
                *TOPICS,
                bootstrap_servers=bootstrap.split(","),
                group_id="reco-sync",
                auto_offset_reset="latest",
                enable_auto_commit=True,
                value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            )
            log.info("listening on %s", TOPICS)
            for msg in consumer:
                v = msg.value or {}
                # order-events fires for every status change; only DELIVERED matters here
                if msg.topic == "order-events" and v.get("status") != "DELIVERED":
                    continue
                log.info("reco trigger from %s: %s", msg.topic, v)
                _schedule_sync()
        except Exception:
            log.exception("kafka consumer error, retrying in 10s")
            threading.Event().wait(10)


def start_listener() -> threading.Thread | None:
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
    if not bootstrap:
        log.info("KAFKA_BOOTSTRAP_SERVERS not set, event-driven sync disabled")
        return None
    t = threading.Thread(target=_consume, args=(bootstrap,), daemon=True)
    t.start()
    return t