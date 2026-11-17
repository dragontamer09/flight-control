# Notes: seat core (holds, bookings, cancel)

- Seat model: `flights.seats_sold`, `holds` + `hold_legs`. Availability = capacity - seats_sold - unexpired, unconsumed hold seats. No sweeper; expiry comes from `expires_at`.
- `holds.consumed` is true for booked and for released (declined payment) holds.
- Schema is idempotent (`CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`). It runs after the catalog COPY, because the CSV has no `seats_sold` column.
- Writers lock `flights` rows with `SELECT ... FOR UPDATE ORDER BY flight_id`. Held seats are summed in a second statement after the locks. In READ COMMITTED a subquery in the locking statement would use the old snapshot and miss holds committed during the lock wait.
- Hold-to-sold conversion, booking insert and idempotency answer share one transaction.
- Idempotency: insert the key first with `ON CONFLICT DO NOTHING`. A concurrent copy blocks on the unique index until the first commits, then replays the stored status and body. Failed outcomes (402, 410) are stored too. Name-count 422 rolls back, so the key is not burned. Same key with a different body returns 422. Keys are never deleted (contract needs 24 h).
- Replays return the first status (201), even after cancel.
- `expires_at` is floored to the second: a hold lives 59 to 60 s, and the reported time never exceeds the real expiry.
- Unknown payment tokens (not `tok_ok_`) are treated as declined (402). The contract leaves this open.
- Errors: `errors.py` maps validation, HTTP and `ApiError` to `{"error","detail"}`. Validation is 422. Types are strict (`"2"` is not an int).
- `db.reset_seat_state(connection)` is for the admin worker's `POST /admin/reset`: it truncates holds, bookings and keys and zeroes `seats_sold`.
- Broke: Dockerfile copied only `app.py db.py` and `.dockerignore` excluded the rest, so the container failed with `No module named 'errors'`. Fixed with `COPY *.py` and `!*.py`.
- Test 3 needs `/admin/reset` and `/admin/flights`, which belong to another worker. I ran it with a throwaway admin module (not committed): 10/10 rounds.
