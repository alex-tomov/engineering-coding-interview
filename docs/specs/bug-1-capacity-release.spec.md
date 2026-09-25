# Spec: BUG-1 — capacity is consumed but never released

Status: Draft — awaiting review
Date: 2026-09-25

## Problem

Slots report themselves full while fewer bookings exist than the slot's
capacity. Reproduced against the live stack:

```
slot-berlin-0900   total_capacity 1   available_capacity 1   reservation_count 0
  → POST /internal/v1/reservations {operation_id: orphaned-op-001, ...}   201
slot-berlin-0900   total_capacity 1   available_capacity 0   reservation_count 1
  → GET /api/v1/bookings                                                  []
  → POST /api/v1/bookings (a real user)                                   500
```

The seat is gone, nobody holds a booking, and no operation exists to get it
back.

## Root causes

Two, independently sufficient.

**1. There is no release path at all.** `available_capacity` is decremented at
`capacity-api/app/routes/reservations.py:32` and incremented **nowhere** —
verified by grep across both services. No `DELETE`, `PATCH`, or `PUT` route
exists. Once a seat is reserved, only `make reset` returns it.

This matters because `booking-api` reserves capacity *before* committing its
own booking row (`bookings.py:30-44`). Any failure in that window — a crash, a
timeout, a database blip — leaves a reservation with no booking. The reported
incident's own trigger, a downstream timeout, produces exactly this state.

**2. `status` never transitions, and the count ignores it.** `Reservation.status`
is only ever written as `"reserved"` (`reservations.py:29`), and `get_slot`
counts reservations without filtering it (`reservations.py:69-71`). The moment
any other status exists, `reservation_count` is wrong. This is latent today and
becomes live the instant release or cancellation is added.

## Users

Patients who cannot book an appointment that is genuinely available, and
operators who cannot tell a real full slot from a leaked one.

## Behaviour

### AC-1 — A reservation whose booking never completes stops holding a seat
Given `booking-api` reserved capacity and then failed before committing
When the reservation is released, or enough time passes
Then the seat is available again and a different user can book it.

### AC-2 — Releasing is idempotent
Given a reservation already released
When it is released again
Then the call succeeds and capacity is **not** credited twice.

### AC-3 — Released capacity does not exceed the original
For any slot, at all times: `available_capacity <= total_capacity`, and
`available_capacity == total_capacity - count(seats actively held)`.

### AC-4 — A completed booking keeps its seat indefinitely
Given a booking committed successfully
When arbitrary time passes
Then its seat is **never** reclaimed. Expiry applies only to reservations that
were never confirmed.

### AC-5 — `reservation_count` reflects seats actually held
Given a slot with one released and one held reservation
When the slot is fetched
Then `reservation_count` is 1, not 2.

### AC-6 — Releasing an unknown operation is not an error
Given an `operation_id` with no reservation
When it is released
Then the call succeeds (nothing to do). A compensating call must never fail
because its subject is already gone.

### AC-7 — A full slot reports 409, not 500
Given a slot with no capacity
When a user attempts to book it
Then `booking-api` returns **409**, not the current 500.

### AC-8 — A committed booking's seat is not lost to a failed confirm
Given a booking that committed successfully
When the call that confirms its seat fails or never happens
Then the intent to confirm survives, is retried, and the seat is not reclaimed
by expiry while the booking exists.

`booking_db` and `capacity_db` cannot share a transaction, so confirming the
seat *after* the booking commits is a dual write with a gap. If the process
dies in between, the booking survives and the reservation does not — the seat
lapses at the TTL while a real appointment exists, and the next user takes it.

That is an **oversell**, and it is strictly worse than the leak AC-1 fixes. So
it cannot be left to best-effort: whatever mechanism confirms the seat must be
as durable as the booking itself. See ADR 0001.

## API contract

New on `capacity-api`:
- `POST /internal/v1/reservations/{operation_id}/release` → 200, idempotent,
  200 even when the operation is unknown (AC-6).
- `POST /internal/v1/reservations/{operation_id}/confirm` → 200, marks the
  seat permanently held. 404 if unknown, 409 if already released.

`GET /internal/v1/slots/{id}` keeps its response shape. `available_capacity`
and `reservation_count` change meaning only in that they now exclude released
and expired reservations.

`booking-api`'s public v1 contract is **unchanged** except AC-7's status code.
`legacy-mobile` must still parse every response.

## Out of scope

- **INC-1**, duplicate bookings on retry. A retry still creates a second
  reservation, because nothing deduplicates on `operation_id` yet. That is a
  separate defect with a separate fix, and mixing them would make both harder
  to review.
- User-initiated cancellation of a *confirmed* booking — FEAT-1, which carries
  a separate API-compatibility problem.
- The concurrent-oversell race on the last seat — FEAT-2.
- Backfilling seats already leaked in an existing database.

## Open questions

1. ~~**How long may an unconfirmed reservation hold a seat?**~~ **Answered:
   15 minutes.** It must exceed `booking-api`'s worst-case commit time and be
   short enough that a leak self-heals. Revisit if commit latency ever
   approaches it.
2. **Should expiry be lazy (evaluated on read) or swept by a background job?**
   The repo has no job runner, which argues for lazy.
3. **Is `available_capacity` stored or derived?** See ADR 0001.
