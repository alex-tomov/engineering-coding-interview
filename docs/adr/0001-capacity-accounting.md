# ADR 0001 — Derive slot availability instead of storing it

Status: Accepted
Date: 2026-09-25
Relates to: BUG-1, `docs/specs/bug-1-capacity-release.spec.md` (open questions 2 and 3)

## Context

`Slot.available_capacity` is a stored counter, decremented when a reservation is
created and never incremented. Reservations must now become releasable, and
unconfirmed ones must expire, so something has to put seats back.

The repository has no job runner, no scheduler, and no migration tool.

## Options

### A. Keep the counter; increment it on release and on expiry
- **For:** Smallest diff. `get_slot` stays a single-row read.
- **Against:** Every future path that frees a seat must remember to increment,
  and any that forgets leaks silently — which is precisely how BUG-1 arose. The
  counter and the reservation rows are two sources of truth that can disagree,
  and nothing detects the disagreement. Expiry also needs a sweeper, because a
  counter cannot expire itself.

### B. Derive availability from active reservations *(chosen)*
`available = total_capacity - count(reservations held)`, where *held* means
confirmed, or pending and not yet expired.
- **For:** Drift becomes structurally impossible — there is one source of truth
  and it is the reservation rows. Expiry needs **no sweeper**: an expired row
  simply stops counting, so the seat returns the moment it lapses. Release is a
  status change, with no arithmetic to get wrong. It makes AC-3 an invariant
  rather than something to test for.
- **Against:** Availability becomes a `COUNT` rather than a column read. Needs an
  index on `(slot_id, status)`. The `available_capacity` column becomes
  vestigial, and `create_all()` cannot drop it — it will linger on existing
  databases until a reset.

### C. Background sweeper releasing expired reservations
- **For:** Keeps the read path as a single-row lookup while still expiring.
- **Against:** Adds a scheduler to a repo that has none, plus a new failure mode
  (sweeper stops, leaks resume silently). It solves expiry but not the
  two-sources-of-truth problem in A.

## Decision

**Option B**, with an explicit release endpoint as a *fast path* rather than the
mechanism of correctness.

- `Reservation.status` becomes a real state machine: `reserved` → `confirmed` or
  `released`. `reserved` rows carry `expires_at`. (`reserved` is kept as the
  initial value rather than renamed to `pending` — an existing contract test
  asserts it and the rename buys nothing.)
- A seat is held iff `status = 'confirmed'`, or `status = 'reserved' AND
  expires_at > now()`.
- `booking-api` reserves, commits its booking, then confirms. On a handled
  failure it releases immediately — that is the fast path, so the common case
  frees the seat in milliseconds instead of after the TTL.
- If the release call is itself lost, expiry reclaims the seat. **Correctness
  does not depend on the compensating call succeeding** — that is the whole
  point, since the failure being compensated for is the same kind of failure
  that can eat the compensation.

TTL: **15 minutes**, pending a product answer (spec open question 1).

`available_capacity` stays in `SlotResponse` so the API shape is unchanged; it
is computed rather than read from the column.

### The dual write, and why confirm is not best-effort (spec AC-8)

Release can be best-effort because expiry backs it up. **Confirm cannot**, and
the asymmetry is easy to miss.

`booking_db` and `capacity_db` cannot share a transaction, so the sequence is
three transactions across two databases:

```
reserve  ─▶ INSERT reservation   [capacity_db]
            INSERT booking       [booking_db]
confirm  ─▶ UPDATE reservation   [capacity_db]
```

A failure between the booking commit and the confirm leaves a durable booking
whose seat is still counting down to expiry. When it lapses the seat is handed
to someone else — an **oversell**, strictly worse than the leak this ADR set
out to fix. Fixing AC-1 naively would therefore introduce a worse bug than the
one being fixed.

The transaction we *can* use is a local one. `booking-api` writes the booking
and a row recording the intent to confirm it **in the same `booking_db`
transaction** — either both land or neither does. A durable intent cannot be
lost with the booking committed, and it is retried until `capacity-api` accepts.
Confirm being idempotent makes at-least-once delivery sufficient.

Rejected alternatives:

- **Confirm before committing the booking** — removes the oversell but
  reintroduces the leak: a confirmed seat with no booking, which nothing
  reclaims because confirmed rows never expire. Trades a bug for a bug.
- **A longer TTL** — narrows the window without closing it, and lengthens how
  long a genuinely leaked seat stays unbookable.
- **Two-phase commit across the services** — the only way to make it truly
  atomic, and disproportionate: new infrastructure, new failure modes, and a
  coupling between services that the separate-databases design exists to avoid.

## Consequences

- `get_slot` and reservation creation both do a `COUNT` over
  `(slot_id, status)`. An index is required; at seed scale it is irrelevant, but
  it should not go in unindexed.
- **Clock dependence.** Expiry is `expires_at > now()` evaluated in Postgres, so
  all comparisons use one clock. Application-side evaluation would introduce
  skew between services.
- A confirmed booking never loses its seat (AC-4), because expiry applies only
  to `reserved` rows — and the outbox ensures the confirm actually happens
  (AC-8).
- The vestigial `available_capacity` column remains on existing databases.
  Documented rather than migrated: this repo has no migration tool at all,
  which is a gap worth raising independently of this change.
- **Does not fix FEAT-2.** Deriving the count does not serialise two concurrent
  reservations for the last seat; both can still pass the check. That needs a
  row lock, and this ADR deliberately leaves it alone — but the derived model is
  compatible with `SELECT ... FOR UPDATE` on the slot when FEAT-2 is taken up.
- Seats already leaked into an existing database are not reclaimed; they have no
  `expires_at`. They clear on `make reset`. A backfill would be needed for a real
  deployment.
