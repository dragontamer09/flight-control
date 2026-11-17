import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Query

from db import pool, reset_seat_state
from errors import ApiError
from seats import booking_json

router = APIRouter(prefix="/admin")

# The restart policy relaunches the container at once. The marker makes the new
# process stay silent until DOWNTIME_SECONDS after the exit, so the server is
# really seen down. It lives in the container filesystem, which survives a restart
# of the same container but not a fresh `up`.
RESTART_MARKER = Path("/tmp/restart-at")
DOWNTIME_SECONDS = 30
EXIT_DELAY_SECONDS = 0.3


def wait_out_restart_downtime():
    try:
        exited_at = float(RESTART_MARKER.read_text())
        RESTART_MARKER.unlink()
    except (OSError, ValueError):
        return
    time.sleep(max(0.0, min(DOWNTIME_SECONDS, exited_at + DOWNTIME_SECONDS - time.time())))


wait_out_restart_downtime()

LIVE_HOLD = "NOT h.consumed AND h.expires_at > clock_timestamp()"


@router.get("/stats")
def stats():
    with pool.connection() as connection:
        bookings = connection.execute(
            "SELECT COUNT(*) FILTER (WHERE status = 'confirmed')::integer AS bookings_confirmed, "
            "COUNT(*) FILTER (WHERE status = 'cancelled')::integer AS bookings_cancelled, "
            "COALESCE(SUM(passenger_count) FILTER (WHERE status = 'confirmed'), 0)::integer AS seats_sold, "
            "COALESCE(SUM(total_cents) FILTER (WHERE status = 'confirmed'), 0)::bigint AS revenue_cents "
            "FROM bookings"
        ).fetchone()
        holds = connection.execute(
            f"SELECT COUNT(*)::integer AS active_holds, COALESCE(SUM(passengers), 0)::integer AS seats_held "
            f"FROM holds h WHERE {LIVE_HOLD}"
        ).fetchone()
    return {**bookings, **holds}


@router.get("/flights/{flight_id}")
def flight(flight_id: str):
    with pool.connection() as connection:
        row = connection.execute(
            "SELECT f.flight_id, f.capacity, f.seats_sold, "
            "COALESCE((SELECT SUM(l.seats) FROM hold_legs l JOIN holds h USING (hold_id) "
            f"WHERE l.flight_id = f.flight_id AND {LIVE_HOLD}), 0)::integer AS seats_held "
            "FROM flights f WHERE f.flight_id = %s",
            (flight_id,),
        ).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "unknown flight")
    row["seats_available"] = row["capacity"] - row["seats_sold"] - row["seats_held"]
    return row


@router.get("/bookings")
def bookings(offset: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=10000)):
    with pool.connection() as connection:
        total = connection.execute("SELECT COUNT(*)::integer AS n FROM bookings").fetchone()["n"]
        rows = connection.execute(
            "SELECT * FROM bookings ORDER BY created_at, booking_id OFFSET %s LIMIT %s",
            (offset, limit),
        ).fetchall()
    return {"total": total, "bookings": [booking_json(r) for r in rows]}


@router.post("/reset")
def reset():
    with pool.connection() as connection:
        with connection.transaction():
            reset_seat_state(connection)
    return {"status": "ok"}


def die():
    RESTART_MARKER.write_text(str(time.time()))
    os._exit(1)


@router.post("/restart", status_code=202)
def restart():
    threading.Timer(EXIT_DELAY_SECONDS, die).start()
    return {"status": "restarting"}
