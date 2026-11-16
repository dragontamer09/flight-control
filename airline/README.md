# Airline service

The airline runs independently of the Flight Control grader. With Docker and
the Compose plugin installed, start it from this directory.

```sh
docker compose up
```

In another terminal, check that the API can reach PostgreSQL.

```sh
curl -fsS -H 'Authorization: Bearer local-airline-key' http://localhost:8080/health
```

## Configuration

Set these variables in an `airline/.env` file or in the shell before starting
Compose. The defaults are for local development.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AIRLINE_API_KEY` | `local-airline-key` | Bearer key the client sends. Set your own key before exposing the API through a tunnel. |
| `AIRLINE_PORT` | `8080` | Host port. Use `http://localhost:<port>` as the local base URL. |
| `AIRLINE_DB_PASSWORD` | `local-airline-db` | Password used by the API and when PostgreSQL initializes a fresh volume. |

Compose publishes only the API on loopback. For a remote grader, point the
tunnel at the local base URL and give the grader the tunnel URL and the same
bearer key. Changing the database password variable on an existing volume
also requires changing the password of the PostgreSQL role.

Compose waits for PostgreSQL before starting the API and restarts the API
container if its process exits. The `airline_data` volume survives
`docker compose down` and a subsequent `docker compose up`. Running
`docker compose down -v` deletes that volume and its data.

## Stack decision for the paper

- **Python 3.12 and FastAPI** use the same language and web framework as the
  grader. FastAPI gives us request validation and JSON responses for the API.
- **PostgreSQL and Psycopg** give us database transactions and row-level locks.
  A future hold or booking can update both legs of an itinerary in one
  transaction, while reservations for unrelated flights can proceed
  concurrently. PostgreSQL adds a separate process and connection management.
  Compose handles startup and gives the database a persistent volume.

The service provides an authenticated health check and loads the flight catalog.
Booking endpoints and `POST /admin/restart` are tracked separately.

## Shared API behavior

Every request requires the configured bearer key. Errors use string `error`
and `detail` fields, including validation errors (`422`), unknown routes (`404`),
and unexpected failures (`500`). Unexpected failures are logged on the server;
their details are not returned to clients. Every response includes
`Server-Timing: app;dur=<milliseconds>`. There is no rate limiter.

Body models must inherit `RequestModel` from `app.py` so wrong JSON types are
rejected rather than coerced (for example, `"2"` is not an integer).
Endpoint-specific bounds and connection rules belong in their respective models
and handlers. Query parameters arrive as text and need their own validation.
For future endpoint responses, use UTC timestamps ending in `Z`, dates in
`YYYY-MM-DD` form, and integer cents for money.

Run the shared API regression check from the repository root with
`python -m pytest tests/test_airline_api.py` in an environment containing
`airline/requirements.txt`, `httpx`, and `pytest`. It uses test-only routes and
does not require PostgreSQL or catalog loading.
