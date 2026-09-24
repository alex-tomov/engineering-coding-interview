# ADR 0001 — Idempotent booking creation

Status: Accepted
Date: 2026-09-24
Relates to: INC-1 (duplicate booking on retry)

## Context

A user submitted a booking, received a timeout, tapped Retry, and ended up with
two bookings and a slot decremented twice.

`legacy-mobile/client.py` retries with the same `Idempotency-Key`, so the client
behaved correctly. The server did not. Three defects were required to produce
the incident, and all three were present:

1. `booking-api` read and stored `Idempotency-Key` but never queried it before
   inserting, and no unique constraint backed the column.
2. `booking-api` minted a fresh `uuid4()` per request and passed it downstream
   as `operation_id`, so a retry carried a *different* token.
3. `capacity-api` never checked `operation_id` for an existing reservation, and
   no unique constraint backed that column either.

`docs/ARCHITECTURE.md` recorded this in 2024-06 as deliberately deferred:
*"Idempotency handling and concurrent-booking protection were deferred to a
future sprint."* The incident is that deferral coming due, not a regression.

## Options

### A. Deduplicate at the `booking-api` edge only
- **For:** Smallest change. Fixes the reported incident directly.
- **Against:** Leaves the window where `booking-api` reserves capacity
  successfully and then fails before committing its own row. The retry creates a
  *second* reservation, orphaning the first — capacity leaks with no booking to
  show for it. This is the mechanism behind BUG-1.

### B. Deduplicate at both edges *(chosen)*
- **For:** Closes the orphan window. Makes the internal API safe for **any**
  retrying caller, not just this one — a property an internal API should have
  regardless of who is calling it today.
- **Against:** Touches two services and two schemas.

### C. Distributed transaction / saga with compensation
- **For:** Handles the general two-phase problem, including releasing capacity
  when a booking is abandoned.
- **Against:** Substantial machinery — an outbox, a reconciler, and a new
  failure surface — for an incident that idempotency alone resolves. The
  capacity-release problem is real but is BUG-1's scope, not INC-1's.

## Decision

**Option B**, implemented test-first.

- `booking-api` looks up `(user_id, idempotency_key)` before any downstream
  call and replays the stored result if found. A unique constraint on that pair
  closes the concurrent-retry race that the lookup alone cannot (it is
  check-then-act); `IntegrityError` is caught and the winner's row returned.
- The booking id is derived as `uuid5(NAMESPACE, f"{user_id}:{idempotency_key}")`
  rather than random, so the `operation_id` sent downstream is **stable across
  retries** with no extra state to store.
- `capacity-api` looks up `operation_id` and replays, with a unique constraint
  and the same `IntegrityError` handling.

Requests **without** an `Idempotency-Key` remain non-idempotent. Postgres treats
NULLs as distinct in unique constraints, so they are unaffected. Rejecting them
with a 400 would be safer, but it is a breaking contract change — see below.

## Consequences

- A retry now returns **201 with the original booking** rather than creating a
  new one. The response body is unchanged, so `legacy-mobile` still parses it;
  its contract tests pass.
- Idempotency is keyed per user. Two different users may reuse the same key
  string without colliding.
- **Not fixed by this change:** capacity is still never *released*. A booking
  abandoned before commit leaves a reservation holding a seat forever. That is
  BUG-1, and it now needs either a compensating release or a reconciler.
- **Not fixed by this change:** two *different* keys racing for the last seat can
  still both succeed — `capacity-api` still does read-then-decrement with no row
  lock. That is FEAT-2.
- **Operational:** both services build their schema with
  `Base.metadata.create_all()`, which does **not** alter existing tables. These
  constraints do not appear on an existing volume. `make reset` is required
  locally, and a real deployment needs a migration tool — there is none in this
  repo.

## Open question for product

Should a request with no `Idempotency-Key` be rejected with 400? The deployed
client always sends one (`legacy-mobile/client.py:46-53`), which suggests the
contract already requires it — but enforcing it server-side would break any
undocumented caller. Left permissive pending an answer.
