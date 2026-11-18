"""Integration tests for the admin endpoints. They need a running airline.

    $env:AIRLINE_URL='http://localhost:8083'; uv run pytest airline/tests/test_admin.py
"""

import csv
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest

URL = os.environ.get("AIRLINE_URL", "http://localhost:8083")
KEY = os.environ.get("AIRLINE_API_KEY", "local-airline-key")
CATALOG = Path(__file__).parents[1] / "catalog" / "flights.csv"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=URL, headers={"Authorization": f"Bearer {KEY}"}, timeout=40) as c:
        try:
            c.get("/health")
        except httpx.TransportError:
            pytest.skip("airline is not running")
        yield c


@pytest.fixture(autouse=True)
def clean(client):
    assert client.post("/admin/reset").status_code == 200


@pytest.fixture
def flights():
    with CATALOG.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return rows


def nonstop(flights, index=0):
    return flights[index]["flight_id"], int(flights[index]["capacity"])


def book(client, itinerary, passengers=1, token="tok_ok_1"):
    h = client.post("/holds", json={"itinerary_id": itinerary, "passengers": passengers})
    assert h.status_code == 201, h.text
    r = client.post(
        "/bookings",
        json={
            "hold_id": h.json()["hold_id"],
            "passengers": [{"name": f"P{i}"} for i in range(passengers)],
            "payment_token": token,
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    return h.json(), r


def connection_itinerary(client):
    """Find a real two-leg itinerary through /search."""
    with CATALOG.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows[:200]:
        r = client.get(
            "/search",
            params={"origin": row["origin"], "destination": "JFK", "date": row["origin_local_date"], "passengers": 1},
        )
        for it in r.json().get("itineraries", []) if r.status_code == 200 else []:
            if it["stops"] == 1:
                return it["itinerary_id"]
    pytest.skip("no connecting itinerary found")


def test_requires_auth():
    assert httpx.get(f"{URL}/admin/stats").status_code == 401


def test_stats_empty_after_reset(client):
    assert client.get("/admin/stats").json() == {
        "bookings_confirmed": 0, "bookings_cancelled": 0, "seats_sold": 0,
        "revenue_cents": 0, "active_holds": 0, "seats_held": 0,
    }


def test_stats_counts_passengers_once_and_revenue_confirmed_only(client):
    itinerary = connection_itinerary(client)
    _, r = book(client, itinerary, passengers=2)
    assert r.status_code == 201
    total = r.json()["total_cents"]
    _, r2 = book(client, itinerary, passengers=1)
    client.delete(f"/bookings/{r2.json()['booking_id']}")
    book(client, itinerary, passengers=3, token="tok_decline_x")
    client.post("/holds", json={"itinerary_id": itinerary, "passengers": 4})
    stats = client.get("/admin/stats").json()
    assert stats == {
        "bookings_confirmed": 1, "bookings_cancelled": 1, "seats_sold": 2,
        "revenue_cents": total, "active_holds": 1, "seats_held": 4,
    }


def test_flight_counts_connection_passenger_on_each_leg(client):
    itinerary = connection_itinerary(client)
    book(client, itinerary, passengers=2)
    client.post("/holds", json={"itinerary_id": itinerary, "passengers": 1})
    for fid in itinerary.split("+"):
        body = client.get(f"/admin/flights/{fid}").json()
        assert body["flight_id"] == fid
        assert (body["seats_sold"], body["seats_held"]) == (2, 1)
        assert body["seats_available"] == body["capacity"] - 3
        assert all(type(body[k]) is int for k in ("capacity", "seats_sold", "seats_held", "seats_available"))


def test_flight_unknown_is_404(client):
    r = client.get("/admin/flights/NOPE-1")
    assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_bookings_export_is_paged_and_ordered(client, flights):
    ids = []
    for i in range(5):
        _, r = book(client, nonstop(flights, i)[0])
        ids.append(r.json()["booking_id"])
    client.delete(f"/bookings/{ids[1]}")
    page = client.get("/admin/bookings", params={"offset": 0, "limit": 500}).json()
    assert page["total"] == 5
    assert [b["booking_id"] for b in page["bookings"]] == ids
    assert page["bookings"][1]["status"] == "cancelled"
    part = client.get("/admin/bookings", params={"offset": 2, "limit": 2}).json()
    assert part["total"] == 5
    assert [b["booking_id"] for b in part["bookings"]] == ids[2:4]


def test_bookings_export_rejects_bad_paging(client):
    assert client.get("/admin/bookings", params={"offset": -1}).status_code == 422
    assert client.get("/admin/bookings", params={"limit": 0}).status_code == 422


def test_reset_clears_everything_and_idempotency(client, flights):
    fid, capacity = nonstop(flights)
    key = uuid.uuid4().hex
    h = client.post("/holds", json={"itinerary_id": fid, "passengers": 1}).json()
    body = {"hold_id": h["hold_id"], "passengers": [{"name": "A"}], "payment_token": "tok_ok_1", "idempotency_key": key}
    assert client.post("/bookings", json=body).status_code == 201
    client.post("/holds", json={"itinerary_id": fid, "passengers": 2})
    assert client.post("/admin/reset").status_code == 200
    assert client.get(f"/admin/flights/{fid}").json()["seats_available"] == capacity
    assert client.get("/admin/bookings").json() == {"total": 0, "bookings": []}
    assert client.post("/bookings", json=body).status_code == 410  # key gone; hold gone


def test_restart_goes_down_comes_back_and_keeps_bookings(client, flights):
    before = []
    for i in range(3):
        _, r = book(client, nonstop(flights, i)[0], passengers=2)
        before.append(r.json())
    old_boot = client.get("/health").json()["boot_id"]
    start = time.monotonic()
    r = client.post("/admin/restart")
    assert r.status_code == 202 and time.monotonic() - start < 1
    saw_down = False
    new_boot = None
    while time.monotonic() - start < 60:
        try:
            body = httpx.get(f"{URL}/health", headers={"Authorization": f"Bearer {KEY}"}, timeout=1).json()
            if body.get("status") == "ok" and body["boot_id"] != old_boot:
                new_boot = body["boot_id"]
                break
        except (httpx.TransportError, ValueError):
            saw_down = True
        time.sleep(0.5)
    healthy_after = time.monotonic() - start
    assert saw_down and new_boot and healthy_after < 60
    assert healthy_after >= 20  # about 30 s of downtime
    for b in before:
        assert client.get(f"/bookings/{b['booking_id']}").json() == b
