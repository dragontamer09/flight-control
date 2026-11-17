from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

CATALOG_DIR = Path(__file__).parent / "catalog"
LOAD_LOCK_ID = 4340

# Opened by the app lifespan; every module takes connections from here.
pool = ConnectionPool(min_size=4, max_size=32, timeout=5, open=False)

SCHEMA = """
CREATE TABLE IF NOT EXISTS airports (
    code text PRIMARY KEY,
    city text NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    tz text NOT NULL,
    hub boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS flights (
    flight_id text PRIMARY KEY,
    flight_number text NOT NULL,
    origin text NOT NULL REFERENCES airports (code),
    destination text NOT NULL REFERENCES airports (code),
    scheduled_departure timestamptz NOT NULL,
    scheduled_arrival timestamptz NOT NULL,
    origin_local_date date NOT NULL,
    capacity integer NOT NULL,
    base_fare_cents integer NOT NULL,
    distance_mi integer NOT NULL
);
CREATE INDEX IF NOT EXISTS flights_origin_date
    ON flights (origin, origin_local_date);
"""

SEAT_SCHEMA = """
ALTER TABLE flights ADD COLUMN IF NOT EXISTS seats_sold integer NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS holds (
    hold_id text PRIMARY KEY,
    itinerary_id text NOT NULL,
    passengers integer NOT NULL,
    price_cents integer NOT NULL,
    total_cents integer NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    consumed boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS holds_live ON holds (expires_at) WHERE NOT consumed;
CREATE TABLE IF NOT EXISTS hold_legs (
    hold_id text NOT NULL REFERENCES holds (hold_id) ON DELETE CASCADE,
    flight_id text NOT NULL REFERENCES flights (flight_id),
    seats integer NOT NULL,
    PRIMARY KEY (hold_id, flight_id)
);
CREATE INDEX IF NOT EXISTS hold_legs_flight ON hold_legs (flight_id);
CREATE TABLE IF NOT EXISTS bookings (
    booking_id text PRIMARY KEY,
    hold_id text NOT NULL UNIQUE REFERENCES holds (hold_id),
    itinerary_id text NOT NULL,
    flight_ids text[] NOT NULL,
    passengers jsonb NOT NULL,
    passenger_count integer NOT NULL,
    total_cents integer NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    cancelled_at timestamptz
);
CREATE INDEX IF NOT EXISTS bookings_created ON bookings (created_at, booking_id);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key text PRIMARY KEY,
    body_hash text NOT NULL,
    status_code integer,
    response jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
"""


def reset_seat_state(connection):
    """Delete every hold, booking and idempotency key and return all seats."""
    connection.execute("TRUNCATE idempotency_keys, bookings, hold_legs, holds")
    connection.execute("UPDATE flights SET seats_sold = 0 WHERE seats_sold <> 0")


def copy_csv(connection, table, path):
    with connection.cursor() as cursor:
        with cursor.copy(f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
            with path.open("rb") as source:
                while chunk := source.read(1 << 20):
                    copy.write(chunk)


def load_catalog():
    """Create the tables and load the catalog once. Later starts change nothing."""
    with psycopg.connect() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOAD_LOCK_ID,))
        connection.execute(SCHEMA)
        if not connection.execute("SELECT EXISTS (SELECT 1 FROM flights)").fetchone()[0]:
            copy_csv(connection, "airports", CATALOG_DIR / "airports.csv")
            copy_csv(connection, "flights", CATALOG_DIR / "flights.csv")
        connection.execute(SEAT_SCHEMA)
