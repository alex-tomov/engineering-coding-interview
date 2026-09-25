# Plan: BUG-1 — release and expire reserved capacity

From `docs/specs/bug-1-capacity-release.spec.md` and ADR 0001.
Branch: `fix/bug-1-standalone`, off `main`.

Each task is one commit, test-first: write the failing test, confirm it fails
for the right reason, implement, green, commit.

---

## Task 1 — Prove the bug

**Test** (`capacity-api/tests/test_reservations.py`)
`test_released_reservation_frees_capacity` — reserve on a capacity-1 slot,
release, assert the seat is back and genuinely re-bookable. Fails today with
404: no release route exists.

**Test** (`booking-api/tests/test_bookings.py`)
`test_full_slot_returns_409` — mock the capacity call to 409, assert
`booking-api` returns 409. Fails today with 500 (AC-7).

No production code. The commit is red, deliberately.

---

## Task 2 — Reservation state machine

`capacity-api/app/models.py`
- `status`: `reserved` | `confirmed` | `released`. `reserved` stays the initial
  value — renaming it would break the existing contract test for nothing.
- New `expires_at: Mapped[datetime | None]`, timezone-aware, nullable
  (confirmed and released rows don't lapse).
- Index on `(slot_id, status)` — ADR 0001 makes availability a `COUNT`, and it
  should not go in unindexed.

Add `RESERVATION_TTL = timedelta(minutes=15)` and one `held_filter()` helper
expressing *"holds a seat"* — `confirmed OR (reserved AND expires_at > now())`
— so the definition lives in exactly one place and the availability check and
`reservation_count` cannot drift apart.

Use `sqlalchemy.func.now()`, not Python's clock: one clock, no skew.

---

## Task 3 — Derive availability

`capacity-api/app/routes/reservations.py`
- `_held_count(db, slot_id)` → `COUNT` under `held_filter()`.
- `create_reservation` checks `total_capacity - _held_count(...) <= 0` instead
  of reading the column, and **stops writing** `slot.available_capacity`
  (currently line 32).
- `get_slot` returns derived values for both `available_capacity` and
  `reservation_count`.
- Set `expires_at` on creation.

`Slot.available_capacity` stays as a column but leaves the write path — ADR
0001 records why it can't be dropped here.

Existing `test_capacity_exhausted` must still pass: it asserts 409 on the
second reservation for a capacity-1 slot, which the derived check preserves.

---

## Task 4 — Release and confirm endpoints

`POST /internal/v1/reservations/{operation_id}/release` → 200
- Unknown operation → 200, no-op (AC-6).
- Already released → 200, no double credit (AC-2). Idempotent by construction,
  since release sets a status rather than incrementing a counter.
- Confirmed → 200 and refuses to release; reclaiming a confirmed seat would
  violate AC-4.

`POST /internal/v1/reservations/{operation_id}/confirm` → 200
- Unknown → 404. Confirming something that does not exist is a real error,
  unlike releasing it.
- Already confirmed → 200, idempotent.
- Released → 409. A seat given away cannot be silently retaken.

**Note:** without INC-1 there is no unique constraint on `operation_id`, so
these look up with `.first()`. Correct for the single-reservation case, which
is the only case that exists when the caller does not retry. Called out in the
spec's out-of-scope section.

---

## Task 5 — `booking-api` drives the lifecycle

`booking-api/app/routes/bookings.py`
- Track whether we reserved; confirm after the booking row commits.
- Release in a `finally` guard when we reserved but never committed. Wrap it so
  a failing release cannot mask the original error — best-effort and logged,
  because correctness rests on expiry, not on this call.
- Map a downstream **409** to 409 rather than 500 (AC-7). Other downstream
  errors keep their current mapping; broadening that is DESIGN-1's scope.

**Confirm must be durable, not best-effort (AC-8).** Release can be best-effort
because expiry backs it up; confirm cannot, because a lost confirm means a
committed booking whose seat expires underneath it — an oversell, worse than
the leak AC-1 fixes.

`booking-api/app/models.py` gains an `outbox` table. The booking row and a row
recording the intent to confirm are written in the **same local transaction**,
so they cannot diverge. Confirm is attempted immediately; the intent row is
deleted only once `capacity-api` accepts, otherwise `attempts` increments and a
later request retries it.

Draining is opportunistic and bounded (a few entries per request) rather than
scheduled — there is no job runner here. Note in the docs that a real
deployment runs it as a worker, so recovery does not depend on the next booking
arriving.

**Deviation risk:** this adds a second table to `booking_db`, so `make reset` is
required for anyone with an existing volume.

---

## Task 6 — Docs

- `docs/ARCHITECTURE.md`: document the reservation lifecycle and the TTL.
- ADR 0001 → Accepted.
- Postman: a collection covering both APIs with a **BUG-1 Regression** folder —
  orphan a seat, release it, assert it came back exactly once.

---

## Verification

1. `make reset` — required; `create_all()` will not add `expires_at` or the
   index to an existing table.
2. `make check` — ruff, mypy, both suites.
3. Manual BUG-1 repro from the spec: orphan a reservation on
   `slot-berlin-0900`, release it, confirm a real user can then book.
4. Expiry with no sweeper: backdate a reserved row's `expires_at`, assert the
   seat returns.
5. `legacy-mobile` contract tests — the v1 response shape must be untouched.

**`make reproduce` will still report the INC-1 duplicate**, and that is
expected on this branch: it demonstrates a different defect, deliberately left
alone. Do not treat it as a failure of this change.

## Not in this change

INC-1's duplicate-on-retry, FEAT-2's oversell race, FEAT-1's cancellation, and
backfilling already-leaked seats.
