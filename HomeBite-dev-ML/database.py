# database.py
import os

from dotenv import load_dotenv
from sqlmodel import create_engine, Session, SQLModel
from sqlalchemy import text

load_dotenv()

# Falls back to a local SQLite file so you can try things before setting up Postgres.
DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite:///dispatch.db"

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)


def get_session():
    with Session(engine) as session:
        yield session


def init_db():
    """Create tables and safely add newer Order fields to existing PostgreSQL databases.

    SQLModel.metadata.create_all() creates missing tables, but does not add columns to
    tables that already exist. This migration addresses that exact upgrade case.
    """
    SQLModel.metadata.create_all(engine)

    if engine.dialect.name == "postgresql":
        # Keep defaults so existing order rows remain valid when columns are added.
        migrations = [
            'ALTER TABLE "order" ADD COLUMN IF NOT EXISTS customer_name VARCHAR',
            'ALTER TABLE "order" ADD COLUMN IF NOT EXISTS customer_phone VARCHAR',
            'ALTER TABLE "order" ADD COLUMN IF NOT EXISTS customer_address VARCHAR',
            'ALTER TABLE "order" ADD COLUMN IF NOT EXISTS customer_note VARCHAR',
            'ALTER TABLE "order" ADD COLUMN IF NOT EXISTS items JSON',
            "ALTER TABLE \"order\" ADD COLUMN IF NOT EXISTS payment_type VARCHAR NOT NULL DEFAULT 'prepaid'",
            'ALTER TABLE "order" ADD COLUMN IF NOT EXISTS amount_to_collect DOUBLE PRECISION NOT NULL DEFAULT 0',
            'ALTER TABLE "order" ADD COLUMN IF NOT EXISTS tip DOUBLE PRECISION NOT NULL DEFAULT 0',
            'ALTER TABLE "order" ADD COLUMN IF NOT EXISTS delivery_code VARCHAR',
            "ALTER TABLE driver ADD COLUMN IF NOT EXISTS external_id VARCHAR",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_driver_external_id ON driver (external_id)",
        ]
        with engine.begin() as connection:
            for statement in migrations:
                connection.execute(text(statement))
