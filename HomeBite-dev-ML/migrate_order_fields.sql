-- Safe PostgreSQL migration for existing HomeBite databases.
-- Run against the database selected by DATABASE_URL if you prefer a manual migration.
ALTER TABLE "order" ADD COLUMN IF NOT EXISTS customer_name VARCHAR;
ALTER TABLE "order" ADD COLUMN IF NOT EXISTS customer_phone VARCHAR;
ALTER TABLE "order" ADD COLUMN IF NOT EXISTS customer_address VARCHAR;
ALTER TABLE "order" ADD COLUMN IF NOT EXISTS customer_note VARCHAR;
ALTER TABLE "order" ADD COLUMN IF NOT EXISTS items JSON;
ALTER TABLE "order" ADD COLUMN IF NOT EXISTS payment_type VARCHAR NOT NULL DEFAULT 'prepaid';
ALTER TABLE "order" ADD COLUMN IF NOT EXISTS amount_to_collect DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE "order" ADD COLUMN IF NOT EXISTS tip DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE "order" ADD COLUMN IF NOT EXISTS delivery_code VARCHAR;
