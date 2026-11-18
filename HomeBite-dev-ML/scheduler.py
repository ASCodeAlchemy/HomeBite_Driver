# scheduler.py -- background job: handles driver timeouts and retries orders with no driver yet
import asyncio
import logging
import os
from datetime import timedelta

from sqlmodel import Session, select

from database import engine
from dispatch import assign_order
from models import Order, utcnow
from redis_client import add_rejection, mark_driver_free

log = logging.getLogger("dispatch.scheduler")

ASSIGNMENT_TIMEOUT_SECONDS = int(os.getenv("ASSIGNMENT_TIMEOUT_SECONDS", "20"))
INTERVAL_SECONDS = 5


def run_dispatch_cycle():
    with Session(engine) as session:
        # 1) Drivers who never answered: release them and put the order back in the queue
        cutoff = utcnow() - timedelta(seconds=ASSIGNMENT_TIMEOUT_SECONDS)
        stale = session.exec(
            select(Order).where(Order.status == "assigned", Order.assigned_at < cutoff)
        ).all()
        for order in stale:
            log.info("Order %s timed out on driver %s", order.id, order.driver_id)
            if order.driver_id is not None:
                add_rejection(order.id, order.driver_id)
                mark_driver_free(order.driver_id)
            order.driver_id = None
            order.status = "pending"
            session.add(order)
        session.commit()

        # 2) Every pending order gets another attempt, oldest first
        pending = session.exec(
            select(Order).where(Order.status == "pending").order_by(Order.created_at)
        ).all()
        for order in pending:
            try:
                assign_order(session, order)
            except Exception:  # one bad order must not block the rest
                log.exception("Could not assign order %s", order.id)


async def dispatch_loop():
    while True:
        await asyncio.sleep(INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(run_dispatch_cycle)
        except Exception:
            log.exception("dispatch cycle failed")

async def reco_sync_loop(interval_hours: float):
    """Nightly recommender sync. Enable with RECO_SYNC_ENABLED=1."""
    from ml.recommendation.sync import run_sync
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            await asyncio.to_thread(run_sync)
        except Exception:
            log.exception("recommender sync failed; keeping the current model")