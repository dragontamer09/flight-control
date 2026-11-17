"""Integration tests for holds and bookings. They need a running airline.

    $env:AIRLINE_URL='http://localhost:8081'; uv run pytest airline/tests
"""

import csv
import os
import random
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

URL = os.environ.get("AIRLINE_URL", "http://localhost:8081")
KEY = os.environ.get("AIRLINE_API_KEY", "local-airline-key")
CATALOG = Path(__file__).parents[1] / "catalog" / "flights.csv"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=URL, headers={"Authorization": f"Bearer {KEY}"}, timeout=10) as c:
        try:
            c.get("/health")
        except httpx.TransportError:
            pytest.skip("airline is not running")
        yield c


@pytest.fixture
def flight():
    """A random nonstop flight with its capacity; fresh per test so tests do not share seats."""
    with CATALOG.open(newline="") as f:
        rows = list(csv.DictReader(f))
    row = random.choice(rows)
    return row["flight_id"], int(row["capacity"]), int(row["base_fare_cents"])


def hold(client, itinerary_id, passengers=1):
    return client.post("/holds", json={"itinerary_id": itinerary_id, "passengers": passengers})


def book(client, hold_id, names, token="tok_ok_1", key=None):
    return client.post(
        "/bookings",
        json={
            "hold_id": hold_id,
            "passengers": [{"name": n} for n in names],
            "payment_token": token,
            "idempotency_key": key or uuid.uuid4().hex,
        },
    )


def test_hold_locks_price_and_returns_contract_shape(client, flight):
    flight_id, _, fare = flight
    r = hold(client, flight_id, 2)
    assert r.status_code == 201
    body = r.json()
    assert body["itinerary_id"] == flight_id
    assert body["passengers"] == 2
    assert body["price_cents"] == fare
    assert body["total_cents"] == 2 * fare
    assert body["expires_at"].endswith("Z")
    assert "Server-Timing" in r.headers


def test_hold_errors(client):
    r = hold(client, "FC0-20270101")
    assert (r.status_code, r.json()["error"]) == (404, "not_found")
    for passengers in (0, 10, "2"):
        r = client.post("/holds", json={"itinerary_id": "x", "passengers": passengers})
        assert r.status_code == 422
        assert set(r.json()) == {"error", "detail"}
    assert client.post("/holds", json={}).status_code == 422


def test_hold_rejects_broken_connection(client, flight):
    flight_id = flight[0]
    assert hold(client, f"{flight_id}+{flight_id}").status_code == 422


def test_booking_flow_with_idempotency_and_cancel(client, flight):
    flight_id = flight[0]
    h = hold(client, flight_id, 2).json()
    key = uuid.uuid4().hex
    first = book(client, h["hold_id"], ["Ada Park", "Lee Park"], key=key)
    assert first.status_code == 201
    booking = first.json()
    assert booking["status"] == "confirmed"
    assert booking["flight_ids"] == [flight_id]
    assert booking["total_cents"] == h["total_cents"]

    repeat = book(client, h["hold_id"], ["Ada Park", "Lee Park"], key=key)
    assert repeat.json() == booking
    changed = book(client, h["hold_id"], ["Ada Park", "Zed Park"], key=key)
    assert changed.status_code == 422

    other = book(client, h["hold_id"], ["Ada Park", "Lee Park"])
    assert other.status_code == 410

    assert client.get(f"/bookings/{booking['booking_id']}").json() == booking
    cancelled = client.delete(f"/bookings/{booking['booking_id']}")
    assert cancelled.status_code == 200
    assert cancelled.json() == {**booking, "status": "cancelled"}
    assert client.delete(f"/bookings/{booking['booking_id']}").json() == cancelled.json()
    assert book(client, h["hold_id"], ["Ada Park", "Lee Park"], key=key).json() == booking
    assert client.get("/bookings/b_nope").status_code == 404
    assert client.delete("/bookings/b_nope").status_code == 404


def test_name_count_must_match_hold(client, flight):
    h = hold(client, flight[0], 2).json()
    assert book(client, h["hold_id"], ["Only One"]).status_code == 422
    assert book(client, h["hold_id"], ["A", "B"]).status_code == 201


def test_declined_payment_releases_hold(client, flight):
    h = hold(client, flight[0]).json()
    assert book(client, h["hold_id"], ["A"], token="tok_decline_1").status_code == 402
    assert book(client, h["hold_id"], ["A"]).status_code == 410
    assert book(client, "h_unknown", ["A"]).status_code == 410


def test_cancel_returns_seats(client, flight):
    flight_id, capacity, _ = flight
    holds = [hold(client, flight_id, 1) for _ in range(capacity)]
    assert [r.status_code for r in holds] == [201] * capacity
    assert hold(client, flight_id, 1).status_code == 409
    booking = book(client, holds[0].json()["hold_id"], ["A"]).json()
    assert hold(client, flight_id, 1).status_code == 409
    client.delete(f"/bookings/{booking['booking_id']}")
    assert hold(client, flight_id, 1).status_code == 201


def test_concurrent_holds_never_oversell(client, flight):
    flight_id, capacity, _ = flight
    with ThreadPoolExecutor(40) as pool:
        codes = list(pool.map(lambda _: hold(client, flight_id, 1).status_code, range(capacity + 25)))
    assert codes.count(201) == capacity
    assert codes.count(409) == 25


def test_concurrent_same_key_creates_one_booking(client, flight):
    h = hold(client, flight[0], 1).json()
    key = uuid.uuid4().hex
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(lambda _: book(client, h["hold_id"], ["A"], key=key), range(20)))
    assert {r.status_code for r in results} == {201}
    assert len({r.json()["booking_id"] for r in results}) == 1
