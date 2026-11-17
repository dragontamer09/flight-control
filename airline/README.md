# Airline service

The airline is the API that Flight Control tests. It runs in Docker with
PostgreSQL. It also serves a front end. One command starts both.

## Prerequisites

- Docker with the Compose plugin. Verified with Docker 29.9.3 and Docker
  Compose v5.7.0 on Windows 11.
- `uv`, to run the Flight Control grader. Verified with uv 0.9.29.

## Start

From the `airline/` directory, run:

```sh
docker compose up -d --build
```

This builds the API, starts PostgreSQL 17, and starts the API. Compose waits
for PostgreSQL before it starts the API. No other step is needed.

Check that the API is up. Replace `8080` with your `AIRLINE_PORT` if you changed
it. The check fails with a 503 when the API cannot reach PostgreSQL.

```sh
curl -fsS -H 'Authorization: Bearer local-airline-key' http://localhost:8080/health
```

## URL and API key

- Base URL: `http://localhost:8080`.
- API key: `local-airline-key`. Every API request sends
  `Authorization: Bearer <key>`.

To use another port or key, create `airline/.env`:

```sh
AIRLINE_PORT=8085
AIRLINE_API_KEY=your-own-key
```

Then run `docker compose up -d --build` again. The table below lists every
setting.

## Front end

Open `http://localhost:8080/ui` in a browser. The page searches flights and
makes reservations. It needs no API key.

## Run the Flight Control tests

From the repo root, run one test at a time:

```sh
uv run flightcontrol test --url http://localhost:8080 --key local-airline-key --test 1
```

Change `--test 1` to `--test 2`, `3`, `4`, or `5` to run the other tests. Use
your own `--url` and `--key` if you changed them. Add `--json` for
machine-readable output. Test 1 runs for 2 minutes. Use `--minutes <n>` to
change that.

## Reset data

Delete all bookings and holds, and keep the flights. Replace `8080` with your
`AIRLINE_PORT` if you changed it:

```sh
curl -fsS -X POST -H 'Authorization: Bearer local-airline-key' http://localhost:8080/admin/reset
```

Delete the database volume and all data. Then start again:

```sh
docker compose down -v
docker compose up -d --build
```

`docker compose down -v` deletes the `airline_data` volume. Use plain
`docker compose down` to stop the stack and keep the data.

## Expose the airline publicly

TODO: Choose a tunnel provider and write the steps to expose the API. Set
`AIRLINE_API_KEY` to your own key before you open a tunnel.

## Configuration

Set these variables in an `airline/.env` file or in the shell before starting
Compose. The defaults are for local development.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AIRLINE_API_KEY` | `local-airline-key` | Bearer key the client sends. Set your own key before exposing the API through a tunnel. |
| `AIRLINE_PORT` | `8080` | Host port. Use `http://localhost:<port>` as the local base URL. |
| `AIRLINE_DB_PASSWORD` | `local-airline-db` | Password used by the API and when PostgreSQL initializes a fresh volume. |

Compose publishes only the API on loopback. Changing the database password
variable on an existing volume also requires changing the password of the
PostgreSQL role.

Compose restarts the API container if its process exits. The `airline_data`
volume survives `docker compose down` and a later `docker compose up`.

## Stack decision for the paper

- **Python 3.12 and FastAPI** use the same language and web framework as the
  grader. FastAPI gives us request validation and JSON responses for the API.
- **PostgreSQL and Psycopg** give us database transactions and row-level locks.
  A future hold or booking can update both legs of an itinerary in one
  transaction, while reservations for unrelated flights can proceed
  concurrently. PostgreSQL adds a separate process and connection management.
  Compose handles startup and gives the database a persistent volume.
