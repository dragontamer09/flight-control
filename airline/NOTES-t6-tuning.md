# Integrated airline grading and Rush profile

The first run on merge commit `b547825` scored 1.00 on each of tests 2–5.
There was no grading failure to reproduce and no service change was needed.
The initial and final measurements are repeat runs of the same code.

Both passes used the Flight Control CLI with `--json` and the default seed
`4340`. Tests ran one at a time against Compose project `fc-t6` at
`http://localhost:8086`. Test 1 uses its default two-minute duration.

## Initial results

| Test | Score | Evidence |
| --- | ---: | --- |
| 2 Rush | 1.00 | All five steps passed. At 40 travelers/s, 1,269 of 1,269 were correct and p95 summed server time was 15.6 ms. No flight mismatches or rule violations. |
| 3 Last Seat | 1.00 | All 10 rounds sold exactly the available seats. Across 400 contenders, 21 booked and 379 received 409. |
| 4 Restart | 1.00 | The app returned with a new boot ID 31.7 s after the restart request. The grader observed 30.4 s of downtime and found zero missing or changed bookings. All 412 travelers in the recovery scoring window were correct. |
| 5 Audit | 1.00 | All 1,750 travelers were correct. Stats, flight counts and export matched the grader's ledger of 500 confirmed and 94 cancelled bookings. |

## Search and hold profile

The first Rush run recorded p95 server times of 4.7 ms for search, 7.1 ms for
hold and 7.4 ms for booking across all five steps. The contract threshold of
500 ms applies to each correct traveler's summed server time at a step.

During the 40 travelers/s step, PostgreSQL held 785 holds, of which 94
were live, and 1,091 hold-leg rows. A sampled `EXPLAIN (ANALYZE, BUFFERS)`
for ATL–PHX on 2027-08-22 used the 28 candidate flight IDs from the schedule.
The search query scanned all 1,091 hold-leg rows and looked up hold records
before applying the live-hold filter in the aggregate. PostgreSQL reported
0.490 ms of planning and 0.551 ms of execution. For a two-leg hold on the
same route, the availability query used the existing `holds_live` and
`hold_legs_flight` indexes and executed in 0.101 ms. The hold plan measures
the availability statement alone. The endpoint's 7.1 ms p95 includes its
flight locking, writes and transaction commit.

The search profile confirms that historical rows add work for this plan.
The measured query and end-to-end timings stayed well below the Rush limit,
so the profile did not justify a query, index or deployment change for this
workload. The snapshot covers one route and the hold history accumulated
within a single Rush run.

The grader recorded 157 search connection errors in the initial Restart run
and 160 in the final run during the intentional outage. The restart endpoint
returned 202 and Docker relaunched the app. PostgreSQL continued running. The
recovery grade measures traffic after the app returned.

## Final results

| Test | Score | Evidence |
| --- | ---: | --- |
| 1 Steady | Unscored | 120 of 120 travelers correct in 120.1 s. |
| 2 Rush | 1.00 | All five steps passed. At 40 travelers/s, 1,171 of 1,171 were correct and p95 summed server time was 14.0 ms. No oversell or partial itinerary. |
| 3 Last Seat | 1.00 | All 10 rounds passed. Exactly 21 of 400 contenders booked, and the other 379 received 409. |
| 4 Restart | 1.00 | The app returned with a new boot ID 31.7 s after the restart request. The grader observed 30.4 s of downtime, verified every booking, and counted 394 of 394 travelers correct in the recovery scoring window. |
| 5 Audit | 1.00 | All 1,776 travelers were correct. The audit found zero mismatches and verified 600 bookings, including 97 cancellations. |

Rush p95 server times across all steps were 4.5 ms for search, 6.6 ms
for hold, and 6.5 ms for booking. At 40 travelers/s, the summed p95 was
15.6 ms initially and 14.0 ms on the repeat run.

The airline integration suite also passed all 19 tests in 51.44 s.

## Raw results

Results are saved locally in `%TEMP%/flight-control-fc-t6-task_cc9d5447d232/`.
The files are `initial-test-*.json`, `final-test-*.json`,
`initial-rush-plans.txt`, and `airline-tests.txt`.
