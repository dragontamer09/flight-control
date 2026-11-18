# T3 admin endpoints (issues #11-#15)

## Design
- All admin routes live in `admin.py` (`/admin` router); `app.py` gains two lines.
- Stats: confirmed bookings only for revenue and `seats_sold` (sum of `passenger_count`, once per passenger). `seats_held`/`active_holds` count holds with `NOT consumed AND expires_at > clock_timestamp()`. Same predicate as the seat code, no sweeper.
- `/admin/flights/{id}`: `seats_sold` is the per-flight column, `seats_held` sums live `hold_legs`; `seats_available` is derived from the same two numbers, so the identity always holds. Unknown id: 404.
- Export: `ORDER BY created_at, booking_id OFFSET/LIMIT` (index `bookings_created`); `limit` 1..10000, default 500; bad paging is 422.
- Reset: `db.reset_seat_state` in one transaction (TRUNCATE + zero `seats_sold`). About 10 ms.
- Restart: 202, then a timer thread calls `os._exit(1)` 0.3 s later (no cleanup, no lifespan shutdown). Docker `restart: unless-stopped` relaunches within ~100 ms, which is too fast to count as down. So `die()` writes the exit time to `/tmp/restart-at` and the new process sleeps at import time until exit + 30 s, before uvicorn binds the port. Port is closed (connection refused) during that time. Grader saw down 30.3 s, back at 32 s, new `boot_id`.
- The marker is in the container filesystem: survives `restart`, not a fresh container. A crash for another reason has no marker and restarts at once.

## PostgreSQL crash and recovery (for the paper)
- Only the app process is killed. The `db` container keeps running, so PostgreSQL does not crash in this test. Confirmed bookings survive because each booking commits before its 201 is sent.
- If Postgres itself were killed: with `fsync=on` and `synchronous_commit=on` (both default) a commit returns only after its WAL record is flushed to disk. On restart Postgres replays WAL from the last checkpoint (redo), so every acknowledged commit is present and uncommitted transactions vanish (MVCC, no undo needed). Recovery time is bounded by checkpoint spacing (`max_wal_size`, `checkpoint_timeout`).
- `synchronous_commit=off` would acknowledge before the flush: a crash can lose up to about 3x `wal_writer_delay` of acknowledged commits, but never corrupts. `fsync=off` can corrupt the cluster after a power loss. We keep the defaults.
- Data is on a named volume (`airline_data`), so it survives container restarts. `docker compose down -v` deletes it.
- Not tested here: killing the db container. Claim is from PostgreSQL documentation (WAL reliability), not from an experiment.

## What broke
- Nothing in the final run. Design risk found early: Docker restarts a dead container in ~100 ms, so a plain `os._exit` shows no downtime. Fixed by the marker delay above.
- Test 4 reports 146 search `RemoteProtocolError`/connect failures during downtime. These are expected and the score still counts 100% correct after return.
