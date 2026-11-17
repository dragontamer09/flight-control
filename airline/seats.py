import hashlib
import json
import secrets
from datetime import timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, StrictStr, conint

from db import pool
from errors import ApiError, error_response

router = APIRouter()

HOLD_SECONDS = 60
MIN_LAYOVER_SECONDS = 45 * 60
MAX_LAYOVER_SECONDS = 240 * 60


class HoldRequest(BaseModel):
    itinerary_id: StrictStr
    passengers: conint(strict=True, ge=1, le=9)


class Passenger(BaseModel):
    name: StrictStr


class BookingRequest(BaseModel):
    hold_id: StrictStr
    passengers: list[Passenger]
    payment_token: StrictStr
    idempotency_key: StrictStr


def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lock_flights(connection, flight_ids):
    """Lock flight rows in flight_id order so concurrent writers cannot deadlock."""
    return connection.execute(
        "SELECT flight_id, origin, destination, scheduled_departure, scheduled_arrival, "
        "capacity, seats_sold, base_fare_cents FROM flights WHERE flight_id = ANY(%s) "
        "ORDER BY flight_id FOR UPDATE",
        (list(flight_ids),),
    ).fetchall()


def held_by_flight(connection, flight_ids):
    """Seats in unexpired, unconsumed holds. Run after the flight locks, in a fresh statement."""
    rows = connection.execute(
        "SELECT l.flight_id, SUM(l.seats)::integer AS held FROM hold_legs l "
        "JOIN holds h USING (hold_id) "
        "WHERE l.flight_id = ANY(%s) AND NOT h.consumed AND h.expires_at > clock_timestamp() "
        "GROUP BY l.flight_id",
        (list(flight_ids),),
    ).fetchall()
    return {row["flight_id"]: row["held"] for row in rows}


def check_connection(legs):
    if len(legs) == 1:
        return
    first, second = legs
    layover = (second["scheduled_departure"] - first["scheduled_arrival"]).total_seconds()
    if first["destination"] != second["origin"]:
        raise ApiError(422, "invalid_itinerary", "second flight does not leave where the first lands")
    if second["destination"] == first["origin"]:
        raise ApiError(422, "invalid_itinerary", "itinerary returns to its origin")
    if not MIN_LAYOVER_SECONDS <= layover <= MAX_LAYOVER_SECONDS:
        raise ApiError(422, "invalid_itinerary", "connection must be 45 to 240 minutes")


@router.post("/holds", status_code=201)
def create_hold(request: HoldRequest):
    flight_ids = request.itinerary_id.split("+")
    if len(flight_ids) > 2:
        raise ApiError(422, "invalid_itinerary", "an itinerary has at most two flights")
    if len(set(flight_ids)) != len(flight_ids):
        raise ApiError(422, "invalid_itinerary", "a flight appears twice")
    hold_id = "h_" + secrets.token_hex(8)
    with pool.connection() as connection:
        connection.row_factory = dict_row
        with connection.transaction():
            found = {row["flight_id"]: row for row in lock_flights(connection, flight_ids)}
            missing = [f for f in flight_ids if f not in found]
            if missing:
                raise ApiError(404, "not_found", f"unknown flight {missing[0]}")
            legs = [found[f] for f in flight_ids]
            check_connection(legs)
            held = held_by_flight(connection, flight_ids)
            for leg in legs:
                fid = leg["flight_id"]
                available = leg["capacity"] - leg["seats_sold"] - held.get(fid, 0)
                if available < request.passengers:
                    raise ApiError(409, "insufficient_seats", f"flight {fid} lacks seats")
            price = sum(leg["base_fare_cents"] for leg in legs)
            total = price * request.passengers
            expires_at = connection.execute(
                "INSERT INTO holds (hold_id, itinerary_id, passengers, price_cents, total_cents, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, "
                "date_trunc('second', clock_timestamp()) + make_interval(secs => %s)) "
                "RETURNING expires_at",
                (hold_id, request.itinerary_id, request.passengers, price, total, HOLD_SECONDS),
            ).fetchone()["expires_at"]
            connection.cursor().executemany(
                "INSERT INTO hold_legs (hold_id, flight_id, seats) VALUES (%s, %s, %s)",
                [(hold_id, fid, request.passengers) for fid in flight_ids],
            )
    return {
        "hold_id": hold_id,
        "itinerary_id": request.itinerary_id,
        "passengers": request.passengers,
        "price_cents": price,
        "total_cents": total,
        "expires_at": iso(expires_at),
    }


def booking_json(row):
    return {
        "booking_id": row["booking_id"],
        "status": row["status"],
        "itinerary_id": row["itinerary_id"],
        "flight_ids": row["flight_ids"],
        "passengers": row["passengers"],
        "total_cents": row["total_cents"],
        "created_at": iso(row["created_at"]),
    }


def body_hash(request: BookingRequest):
    canonical = json.dumps(request.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.post("/bookings")
def create_booking(request: BookingRequest):
    digest = body_hash(request)
    with pool.connection() as connection:
        connection.row_factory = dict_row
        with connection.transaction():
            # Insert first: a concurrent copy of this key blocks on the unique index until
            # this transaction commits, then reads the stored answer.
            inserted = connection.execute(
                "INSERT INTO idempotency_keys (idempotency_key, body_hash) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING RETURNING 1",
                (request.idempotency_key, digest),
            ).fetchone()
            if inserted is None:
                previous = connection.execute(
                    "SELECT body_hash, status_code, response FROM idempotency_keys "
                    "WHERE idempotency_key = %s",
                    (request.idempotency_key,),
                ).fetchone()
                if previous["body_hash"] != digest:
                    raise ApiError(422, "idempotency_conflict", "key was used with a different body")
                return JSONResponse(previous["response"], status_code=previous["status_code"])

            status_code, body = place_booking(connection, request)
            connection.execute(
                "UPDATE idempotency_keys SET status_code = %s, response = %s "
                "WHERE idempotency_key = %s",
                (status_code, Jsonb(body), request.idempotency_key),
            )
    return JSONResponse(body, status_code=status_code)


def place_booking(connection, request):
    """Return (status, body). Raises ApiError to roll back without storing the answer."""
    hold = connection.execute(
        "SELECT hold_id, itinerary_id, passengers, total_cents, consumed, "
        "expires_at > clock_timestamp() AS live FROM holds WHERE hold_id = %s FOR UPDATE",
        (request.hold_id,),
    ).fetchone()
    if hold is None or hold["consumed"] or not hold["live"]:
        return 410, {"error": "hold_gone", "detail": "hold is expired, unknown or already booked"}
    if len(request.passengers) != hold["passengers"]:
        raise ApiError(422, "passenger_mismatch", "passenger count differs from the hold")
    if not request.payment_token.startswith("tok_ok_"):
        connection.execute("UPDATE holds SET consumed = true WHERE hold_id = %s", (hold["hold_id"],))
        return 402, {"error": "payment_declined", "detail": "payment token was declined"}

    flight_ids = hold["itinerary_id"].split("+")
    lock_flights(connection, flight_ids)
    connection.execute("UPDATE holds SET consumed = true WHERE hold_id = %s", (hold["hold_id"],))
    connection.execute(
        "UPDATE flights SET seats_sold = seats_sold + %s WHERE flight_id = ANY(%s)",
        (hold["passengers"], flight_ids),
    )
    row = connection.execute(
        "INSERT INTO bookings (booking_id, hold_id, itinerary_id, flight_ids, passengers, "
        "passenger_count, total_cents, status) VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed') "
        "RETURNING *",
        (
            "b_" + secrets.token_hex(8),
            hold["hold_id"],
            hold["itinerary_id"],
            flight_ids,
            Jsonb([p.model_dump() for p in request.passengers]),
            hold["passengers"],
            hold["total_cents"],
        ),
    ).fetchone()
    return 201, booking_json(row)


@router.get("/bookings/{booking_id}")
def get_booking(booking_id: str):
    with pool.connection() as connection:
        connection.row_factory = dict_row
        row = connection.execute(
            "SELECT * FROM bookings WHERE booking_id = %s", (booking_id,)
        ).fetchone()
    if row is None:
        return error_response(404, "not_found", "unknown booking")
    return booking_json(row)


@router.delete("/bookings/{booking_id}")
def cancel_booking(booking_id: str):
    with pool.connection() as connection:
        connection.row_factory = dict_row
        with connection.transaction():
            row = connection.execute(
                "SELECT * FROM bookings WHERE booking_id = %s FOR UPDATE", (booking_id,)
            ).fetchone()
            if row is None:
                return error_response(404, "not_found", "unknown booking")
            if row["status"] == "confirmed":
                lock_flights(connection, row["flight_ids"])
                connection.execute(
                    "UPDATE flights SET seats_sold = seats_sold - %s WHERE flight_id = ANY(%s)",
                    (row["passenger_count"], row["flight_ids"]),
                )
                row = connection.execute(
                    "UPDATE bookings SET status = 'cancelled', cancelled_at = clock_timestamp() "
                    "WHERE booking_id = %s RETURNING *",
                    (booking_id,),
                ).fetchone()
    return booking_json(row)
