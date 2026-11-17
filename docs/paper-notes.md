# Flight Control Airline Paper Notes

These notes support writing the 3-page check-in paper for GitHub issue #21.

## 1. Design chosen and why

- **Language and runtime**: Python 3.12 running under Docker (`airline/Dockerfile:1`, base image `python:3.12-slim`). Python matches the Flight Control grader ecosystem, enables rapid iteration, and provides native library support for FastAPI and Psycopg.
- **Web framework**: FastAPI with Uvicorn ASGI server (`airline/Dockerfile:9`, `airline/app.py:27`). FastAPI provides automated request schema validation via Pydantic (`airline/seats.py:22-35`), native JSON serialization, clean dependency injection, and standardized HTTP error status code generation.
- **Database and driver**: PostgreSQL 17 (`airline/compose.yaml:18`) with Psycopg 3 (`psycopg` 3.2.1) and Psycopg Pool (`psycopg_pool` 3.2.1) (`airline/requirements.txt:1-4`, `airline/db.py:9`). PostgreSQL provides ACID transactions, explicit row-level locking (`SELECT ... FOR UPDATE`), partial indexes, and durable write-ahead logging (WAL).
- **Connection pooling**: A single shared `ConnectionPool` with `min_size=4`, `max_size=32`, `timeout=5`, and `kwargs={"row_factory": dict_row}` manages database connections across all application modules (`airline/db.py:9`). The pool is opened and closed within the FastAPI lifespan context manager (`airline/app.py:20-25`).
- **Database schema (tables and purposes)**:
  - `airports` (`airline/db.py:12-19`): Stores static airport metadata including `code` (PK, text), `city` (text), `lat` (double precision), `lon` (double precision), `tz` (text), and `hub` (boolean). Loaded once from `catalog/airports.csv` via PostgreSQL `COPY` (`airline/db.py:77-88`).
  - `flights` (`airline/db.py:20-33`): Stores static flight catalog data and dynamic sold seat counts. Columns are `flight_id` (PK, text), `flight_number` (text), `origin` (FK to `airports`), `destination` (FK to `airports`), `scheduled_departure` (timestamptz), `scheduled_arrival` (timestamptz), `origin_local_date` (date), `capacity` (integer), `base_fare_cents` (integer), `distance_mi` (integer), and dynamic `seats_sold` (integer NOT NULL DEFAULT 0, added in `airline/db.py:35`). An index exists on `(origin, origin_local_date)` (`airline/db.py:32-33`).
  - `holds` (`airline/db.py:36-46`): Stores active and historical seat reservation holds. Columns are `hold_id` (PK, text), `itinerary_id` (text), `passengers` (integer), `price_cents` (integer), `total_cents` (integer), `expires_at` (timestamptz), `created_at` (timestamptz DEFAULT `clock_timestamp()`), and `consumed` (boolean DEFAULT false). Has partial index `holds_live` on `(expires_at) WHERE NOT consumed` (`airline/db.py:46`).
  - `hold_legs` (`airline/db.py:47-53`): Maps holds to individual flight legs for multi-leg connecting itineraries. Columns are `hold_id` (FK to `holds` ON DELETE CASCADE), `flight_id` (FK to `flights`), and `seats` (integer). Composite PK is `(hold_id, flight_id)`. Has index `hold_legs_flight` on `(flight_id)` (`airline/db.py:53`).
  - `bookings` (`airline/db.py:54-67`): Stores confirmed and cancelled customer bookings. Columns are `booking_id` (PK, text), `hold_id` (UNIQUE FK to `holds`), `itinerary_id` (text), `flight_ids` (text[]), `passengers` (jsonb), `passenger_count` (integer), `total_cents` (integer), `status` (text, e.g. `'confirmed'` or `'cancelled'`), `created_at` (timestamptz DEFAULT `clock_timestamp()`), and `cancelled_at` (timestamptz). Has index `bookings_created` on `(created_at, booking_id)` (`airline/db.py:67`).
  - `idempotency_keys` (`airline/db.py:68-75`): Stores booking idempotency keys for at-least-once retry semantics. Columns are `idempotency_key` (PK, text), `body_hash` (text SHA-256 digest), `status_code` (integer), `response` (jsonb), and `created_at` (timestamptz DEFAULT `clock_timestamp()`).
- **Module layout**:
  - `airline/app.py`: Entrypoint; creates FastAPI app, configures lifespan hook, registers authentication middleware, attaches `/health` and `/ui` endpoints, mounts routers.
  - `airline/db.py`: Connection pool configuration, catalog migration and CSV ingestion routines, table DDL definitions, `reset_seat_state` helper.
  - `airline/search.py`: In-memory static flight routing indices (`first_flights`, `connections`, `connection_times`) with binary search connecting route planning, plus dynamic database seat availability lookup for `GET /search`.
  - `airline/seats.py`: Transactional core for seat reservation logic: `POST /holds`, `POST /bookings`, `GET /bookings/{booking_id}`, and `DELETE /bookings/{booking_id}`.
  - `airline/admin.py`: Operational endpoints for grading harness: `GET /admin/stats`, `GET /admin/flights/{flight_id}`, `GET /admin/bookings`, `POST /admin/reset`, and `POST /admin/restart`.
  - `airline/errors.py`: Custom `ApiError` class and FastAPI exception handlers standardizing errors into `{"error": "<code>", "detail": "<text>"}` responses.
  - `airline/static/index.html`: Responsive web user interface for travelers.
  - `airline/compose.yaml`: Multi-container definitions for `app`, `db` (Postgres 17 with healthcheck), and opt-in Cloudflare `tunnel`.
- **Rejected alternatives**:
  - *Counter columns with background expiration sweeper*: Rejected maintaining a `seats_held` column on `flights` decremented by an asynchronous sweeper worker. A background sweeper adds an independent moving part that can fail, lag under high load, or leak held seat counts across server restarts and process crashes. Instead, live hold seats are derived on demand from `holds` where `NOT consumed AND expires_at > clock_timestamp()`.
  - *In-memory seat state / cache*: Rejected storing holds and booking state in memory (like the toy airline) or external cache (Redis). In-memory state cannot survive process restart tests, breaks multi-worker scaling, and risks split-brain or double-allocation under crash scenarios.
  - *Opening a fresh database connection per request*: Initial search implementation established a new connection via `psycopg.connect()` on every incoming request. Burst testing revealed high connection latency (p95 259 ms under 40 clients). Replaced with a shared `psycopg_pool.ConnectionPool`.

## 2. What broke during the build and how it was fixed

- **Merge row_factory dict_row connection pool bug**:
  - *Symptom with numbers*: After merging search and seat core branches (commit `adef305` and `5bcea6f`), about 65% of searches returned HTTP 500 (`KeyError` on flight id). Integrated test 1 showed only 42 of 119 travelers correct.
  - *Root cause*: `airline/seats.py` overrode the shared pooled connection's configuration with `connection.row_factory = dict_row` inside hold and booking handlers without restoring it upon check-in to the pool. When `airline/search.py` received a recycled connection from the pool, it expected tuple rows (`row[0]`, `row[1]`) but received dictionary rows (`dict`), raising a `KeyError`.
  - *Fix (commit d541678)*: Configured `kwargs={"row_factory": dict_row}` globally on the shared `ConnectionPool` initialization in `airline/db.py:9`, removed per-request overrides in `airline/seats.py`, and refactored search availability lookups to consistently use dict keys (`row["flight_id"]`, `row["available"]`) in `airline/search.py:68-71`.
  - *Evidence after*: Test 1 showed 118 of 118 travelers correct (later confirmed at 120 of 120 in final run `2a8bd5f`).
- **Search connection latency bottleneck**:
  - *Symptom with numbers*: Under burst testing with 40 concurrent clients, search p95 latency reached 259 ms.
  - *Root cause*: `airline/search.py` opened a fresh TCP and TLS/authentication connection to PostgreSQL via `psycopg.connect()` on every search request instead of utilizing pooled connections.
  - *Fix (commit d541678)*: Merged `search.py` into the shared `db.pool` connection pool, acquiring connections using `with pool.connection() as connection:`.
  - *Evidence after*: In test 2 (Rush profile at 40 travelers/s), search p95 server time dropped to 4.5 ms, hold p95 was 6.6 ms, booking p95 was 6.5 ms, and overall summed server time p95 reached 14.0 ms (down from 15.6 ms initially), well below the 500 ms contract ceiling.
- **Docker build missing Python modules**:
  - *Symptom with numbers*: Container exited immediately on boot with `ModuleNotFoundError: No module named 'errors'`.
  - *Root cause*: `airline/Dockerfile` contained `COPY app.py db.py ./`, and `airline/.dockerignore` excluded all other Python files by default with `*` and explicit whitelists. When modular code `errors.py` and `seats.py` were added in commit `0491640`, they were omitted from the build image.
  - *Fix (commit 0491640)*: Updated `airline/Dockerfile` to `COPY *.py ./` and modified `airline/.dockerignore` with `!*.py`.
  - *Evidence after*: Container built cleanly and started FastAPI application without import failures.
- **Docker instant restart during restart test**:
  - *Symptom with numbers*: Flight Control grader test 4 failed because Docker restart policy `restart: unless-stopped` relaunched the app container in approximately 100 ms. Grader requires the server to be observed down for approximately 30 seconds.
  - *Root cause*: A bare `os._exit(1)` caused Docker to restart the service almost immediately; the grader polling `/health` every 1 second failed to witness the service down.
  - *Fix (commit 48fa428)*: In `airline/admin.py:18-32,84-93`, `die()` records the current timestamp to `/tmp/restart-at` on the container filesystem before invoking `os._exit(1)` after 0.3 s. Upon relaunch, `wait_out_restart_downtime()` reads the timestamp and executes `time.sleep()` until 30 seconds have elapsed from exit time, before binding the HTTP port.
  - *Evidence after*: Grader observed 30.4 s of downtime, verified server recovery at 31.7 s with a fresh `boot_id`, and scored 1.00 on test 4 with 394 of 394 recovery travelers correct.
- **Unknown payment token handling**:
  - *Symptom with numbers*: Initially, unknown payment tokens (tokens not starting with `tok_ok_`) triggered hold consumption and returned HTTP 402 `payment_declined`.
  - *Root cause*: The API contract did not define behavior for non-standard tokens. Treating malformed tokens as payment declines incorrectly burned the customer hold and consumed the idempotency key.
  - *Fix (commit 81b4f2b)*: Updated `airline/seats.py:180-184` to validate payment tokens against `("tok_ok_", "tok_decline_")`. Any token not matching either prefix raises `ApiError(422, "invalid_payment_token")`, rolling back the transaction, preserving the hold, and leaving the idempotency key available for valid retries.
  - *Evidence after*: Unit tests in `airline/tests/test_seats.py:90-95` verify that unknown tokens return 422 while holds remain active and bookable. Note that the grader test suite only sends `tok_ok_*` or `tok_decline_*`.

## 3. How the system keeps a seat from being sold twice

### Step-by-step walkthrough of POST /holds (`airline/seats.py:76-118`)
1. Request parsing and validation: `HoldRequest` enforces valid Pydantic types (`passengers` between 1 and 9, `itinerary_id` containing 1 or 2 flight IDs separated by `+`). Duplicate flight IDs or itineraries longer than 2 legs return 422 (`invalid_itinerary`).
2. Generate random hold token: Creates unique `hold_id` (`"h_" + secrets.token_hex(8)`).
3. Transaction begin: Acquires a connection from `pool` and opens an explicit PostgreSQL transaction block (`with connection.transaction():`).
4. Lock flight records: Calls `lock_flights(connection, flight_ids)` executing `SELECT ... FROM flights WHERE flight_id = ANY(%s) ORDER BY flight_id FOR UPDATE` (`airline/seats.py:42-48`). Sorting the flight IDs lexicographically guarantees deterministic lock acquisition order across concurrent transactions, preventing deadlocks on multi-leg flights.
5. Itinerary connection validation: Validates connecting layover timing (between 45 and 240 minutes) and route continuity (`airline/seats.py:63-73`).
6. Query live held seats: Calls `held_by_flight(connection, flight_ids)` in a separate statement (`airline/seats.py:51-60`). It queries `hold_legs` joined to `holds` where `flight_id = ANY(%s) AND NOT h.consumed AND h.expires_at > clock_timestamp()`. Running this query in a separate statement after locking the flight rows ensures the READ COMMITTED transaction snapshot sees all holds committed by concurrent transactions prior to the lock acquisition.
7. Availability check: For each flight leg, calculates:
   `available = capacity - seats_sold - held`
   If `available < requested_passengers`, raises `ApiError(409, "insufficient_seats")`, rolling back the transaction.
8. Store hold and hold legs: Computes price and sets `expires_at = date_trunc('second', clock_timestamp()) + interval '60 seconds'`. Inserts records into `holds` and `hold_legs`.
9. Transaction commit: Commits transaction, releasing row locks on `flights`, and returns 201 with hold details.

### Step-by-step walkthrough of POST /bookings (`airline/seats.py:136-197`)
1. Request validation and payload digest: Validates passenger list and computes SHA-256 digest of normalized request payload (`airline/seats.py:121-127`).
2. Transaction begin: Acquires connection and starts transaction block.
3. Idempotency reservation: Executes `INSERT INTO idempotency_keys (idempotency_key, body_hash) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING 1` (`airline/seats.py:143-146`).
   - If key already exists (concurrent duplicate or retry): Queries `idempotency_keys` for `body_hash`, `status_code`, and `response`. If `body_hash` does not match digest, raises `ApiError(422, "idempotency_conflict")`. If body matches, returns the previously recorded HTTP response and status code.
   - If key is new: Concurrent requests with the same key block on the PostgreSQL primary key unique index until this transaction completes.
4. Validate hold: Calls `place_booking` which executes `SELECT ... FROM holds WHERE hold_id = %s FOR UPDATE` (`airline/seats.py:168-172`).
   - If hold does not exist, `consumed = true`, or `expires_at <= clock_timestamp()`, returns 410 (`hold_gone`).
   - If passenger count does not match hold, raises `ApiError(422, "passenger_mismatch")` (rolls back transaction; idempotency key is not burned).
5. Payment validation:
   - If payment token does not start with `tok_ok_` or `tok_decline_`, raises `ApiError(422, "invalid_payment_token")` (rolls back transaction; hold remains active; idempotency key is not burned).
   - If payment token starts with `tok_decline_`, marks `holds.consumed = true`, commits, and returns 402 (`payment_declined`).
6. Lock flights and convert seats:
   - Locks itinerary flights via `lock_flights(connection, flight_ids)` with `ORDER BY flight_id FOR UPDATE`.
   - Sets `holds.consumed = true` (`airline/seats.py:186`).
   - Increments sold counter: `UPDATE flights SET seats_sold = seats_sold + %s WHERE flight_id = ANY(%s)` (`airline/seats.py:187-190`).
   - Inserts booking into `bookings` with status `'confirmed'`.
7. Record idempotency response: Updates `idempotency_keys` row with HTTP status 201 and JSON booking payload (`airline/seats.py:158-162`).
8. Transaction commit: Commits atomically. Concurrent blocked idempotency requests now unblock and read the stored 201 response.

### Empirical verification from test results
- **Test 3 (Last Seat)**: 10 rounds executed with 40 concurrent contenders competing for 1 to 3 remaining seats (400 contenders total). Exactly 21 contenders booked remaining inventory across all 10 rounds; the remaining 379 contenders received HTTP 409 (`insufficient_seats`). Zero oversell occurred (Score: 1.00).
- **Test 5 (Audit)**: Grader executed 10 travelers/s for 3 minutes (1,776 travelers total) with duplicate requests, race conditions, cancellations, declined payments, and abandoned holds. Grader verified 600 bookings (including 97 cancellations) with zero mismatches between internal ledger and airline state (Score: 1.00).

## 4. Database crash and recovery behaviour on restart

- **Mechanism of /admin/restart (`airline/admin.py:18-32,84-93`)**:
  - `POST /admin/restart` schedules `die()` on a 0.3-second timer and immediately returns HTTP 202 `{"status": "restarting"}`.
  - `die()` writes the current UNIX epoch to `/tmp/restart-at` on the container local filesystem and terminates the process immediately via `os._exit(1)`. This bypasses Python cleanup handlers, FastAPI shutdown hooks, and connection pool draining.
  - Docker Compose detects container exit under `restart: unless-stopped` and relaunches the container.
  - On startup, `admin.py` executes `wait_out_restart_downtime()`, reading `/tmp/restart-at` and sleeping until exactly 30 seconds have elapsed since exit, after which it unlinks the marker file and proceeds with startup.
  - Uvicorn initializes, runs lifespan database checks, generates a new UUID `BOOT_ID`, and binds port 8080.
- **What survives and why**:
  - The PostgreSQL database runs as a separate container service (`db`) backed by a persistent named volume (`airline_data:/var/lib/postgresql/data`, `airline/compose.yaml:26-28`).
  - All confirmed bookings survive because every successful `POST /bookings` commits before sending HTTP 201.
  - Uncommitted transactions active during process failure are automatically rolled back by PostgreSQL when the application socket drops.
- **Durability and WAL architecture (from PostgreSQL documentation; marked as untested)**:
  - PostgreSQL uses write-ahead logging (WAL) with default configuration parameters `fsync=on` and `synchronous_commit=on`.
  - Under these settings, a transaction commit does not return success to the client until its WAL records are flushed to persistent non-volatile disk.
  - If the database container or host loses power, PostgreSQL recovers on restart by executing REDO recovery from the last checkpoint, replaying committed transactions from WAL log segments.
  - Uncommitted transactions are ignored via MVCC transaction status checks in `pg_xact`.
  - Recovery time is bounded by PostgreSQL checkpoint intervals (`max_wal_size`, `checkpoint_timeout`).
  - *Testing status*: UNTESTED. Test 4 terminates only the application process (`app` container). The PostgreSQL database container (`db`) was never killed during testing.
- **Empirical measurements in Test 4 (Restart test)**:
  - Downtime measured by grader: 30.4 seconds (HTTP connection refused).
  - Recovery time: App returned healthy with a new `boot_id` after 31.7 seconds.
  - Traffic during downtime: Grader recorded 160 search connection errors (`RemoteProtocolError`/connect failures).
  - Post-restart integrity: Grader verified 100% of confirmed bookings made prior to restart; zero bookings were missing or altered. All 394 travelers in the recovery scoring window passed (Score: 1.00).

## 5. Test results table for tests 1-5

Data taken from final runs documented in `airline/NOTES-t6-tuning.md` and commit `6c87ce5`:

| Test | Score | Evidence |
| --- | ---: | --- |
| 1 Steady | Unscored | 120 of 120 travelers correct in 120.1 s duration. Zero 5xx errors or broken contract rules. |
| 2 Rush | 1.00 | Passed all 5 arrival rate steps (5, 10, 20, 30, 40 travelers/s). At peak 40 travelers/s, 1,171 of 1,171 travelers were correct. Summed server time p95 was 14.0 ms (contract limit: 500 ms). Zero oversells or partial itineraries. Individual p95 times across all steps: search 4.5 ms, hold 6.6 ms, booking 6.5 ms. |
| 3 Last Seat | 1.00 | Passed all 10 rounds. Exactly 21 of 400 contenders booked remaining seats across all rounds; the other 379 contenders received HTTP 409. Zero oversell. |
| 4 Restart | 1.00 | App restarted with fresh `boot_id` 31.7 s after restart trigger. Grader recorded 30.4 s of downtime. Zero confirmed bookings lost or modified. 394 of 394 travelers correct in recovery scoring window. |
| 5 Audit | 1.00 | 1,776 of 1,776 travelers correct. Grader audit verified 600 bookings (including 97 cancellations) with zero state mismatches across stats, per-flight seat totals, and booking exports. |

Suite verification: The airline automated test suite (`pytest`) passed all 19 tests in 51.44 s.

## 6. Open questions for the user to decide while writing the paper

1. **Trade-off presentation for hold expiration design**: Should the paper emphasize simplicity (omitting background sweepers in favor of on-demand predicate evaluation `expires_at > clock_timestamp()`) as a deliberate reliability choice, or address potential performance degradation if millions of historical expired holds accumulate over months?
2. **PostgreSQL crash test omission**: Should the paper include an explicit section explaining why killing the database container was left untested, or describe PostgreSQL WAL durability guarantees as standard practice supported by container volume mounts?
3. **Connection pooling sizing**: The pool configuration sets `min_size=4` and `max_size=32`. Under the 40 travelers/s load step in test 2, server latency remained at 14.0 ms. Should the paper discuss whether connection pooling limits would need adjustment under higher concurrency (e.g., hundreds of concurrent clients)?
4. **Cloudflare quick tunnel vs named tunnel**: The deployment uses Cloudflare quick tunnels (`trycloudflare.com`) via Docker Compose profile `tunnel`. Quick tunnel URLs change whenever the container recreates. Should the paper discuss why quick tunnels were chosen over stable named tunnels or direct reverse proxies?
5. **Idempotency key retention policy**: `idempotency_keys` table retains keys indefinitely, satisfying the contract requirement of 24 hours. Should the paper propose a future partitioning or TTL pruning strategy for long-term production use?
