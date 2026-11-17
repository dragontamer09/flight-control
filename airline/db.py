from pathlib import Path

import psycopg

CATALOG_DIR = Path(__file__).parent / "catalog"
LOAD_LOCK_ID = 4340

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


def copy_csv(connection, table, path):
    columns = '(flight_id, flight_number, origin, destination, scheduled_departure, scheduled_arrival, origin_local_date, capacity, base_fare_cents, distance_mi)' if table == 'flights' else ''
    with connection.cursor() as cursor:
        with cursor.copy(f"COPY {table} {columns} FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
            with path.open("rb") as source:
                while chunk := source.read(1 << 20):
                    copy.write(chunk)


def load_catalog():
    """Create the tables and load the catalog once. Later starts change nothing."""
    with psycopg.connect() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOAD_LOCK_ID,))
        connection.execute(SCHEMA)
        # Temporary search compatibility schema; replace with T1's schema on merge.
        connection.execute("""
            ALTER TABLE flights ADD COLUMN IF NOT EXISTS seats_sold integer NOT NULL DEFAULT 0;
            CREATE TABLE IF NOT EXISTS holds (
                hold_id text PRIMARY KEY, itinerary_id text NOT NULL, passengers integer NOT NULL,
                price_cents integer NOT NULL, total_cents integer NOT NULL,
                expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
                consumed boolean NOT NULL DEFAULT false
            );
            CREATE TABLE IF NOT EXISTS hold_legs (
                hold_id text NOT NULL REFERENCES holds (hold_id),
                flight_id text NOT NULL REFERENCES flights (flight_id), seats integer NOT NULL,
                PRIMARY KEY (hold_id, flight_id)
            );
            CREATE INDEX IF NOT EXISTS hold_legs_flight ON hold_legs (flight_id);
        """)
        if connection.execute("SELECT EXISTS (SELECT 1 FROM flights)").fetchone()[0]:
            return
        copy_csv(connection, "airports", CATALOG_DIR / "airports.csv")
        copy_csv(connection, "flights", CATALOG_DIR / "flights.csv")
