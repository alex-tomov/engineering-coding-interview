# Working session log — BUG-1

Condensed record of the session. Assistant replies are shortened; commands and
outputs are verbatim from the run.

Repo: `engineering-coding-interview` · Date: 2026-09-25 · Times are wall clock.

**BUG-1 — capacity reserved but never released. 12:05 → 13:04, 59 min:**
reproduce → spec → failing test → fix → verify, with ~40% spent understanding
the problem before touching code.

---

### 12:05 — Me

> Clone this repo and investigate the project.

### 12:09 — Claude *(~3.5 min: clone, read every file, map the call path)*

Appointment booking, two FastAPI services over **separate Postgres databases** —
no shared DB, no message bus, no migration tool.

```
legacy-mobile (contract model)
      │  POST /api/v1/bookings   (X-User-ID, Idempotency-Key)
      ▼
booking-api  :18080  ──HTTP──▶  capacity-api  :18081
  booking_db                      capacity_db
  Booking                         Slot, Reservation
```

- Python 3.12, SQLAlchemy 2.0, Pydantic 2, ruff + mypy, all in Docker.
- `make up / test / lint / typecheck / check / reproduce / reset`.
- Both services build schema with `Base.metadata.create_all()` at startup —
  **no Alembic**. `capacity-api` seeds 3 slots; `slot-berlin-0900` has capacity
  **1**, which is the fixture for the concurrency cases.
- Auth is a bare `X-User-ID` header — deliberately out of scope.
- `docs/ARCHITECTURE.md` is dated 2024-06 and pre-confesses a known deferral:
  *"Idempotency handling and concurrent-booking protection were deferred to a
  future sprint."*

Layering is clean and consistent: `controller → service → model`. Nothing
enforces it — convention only, no lint rule.

---

### 12:10 — Me

> What are the features?

### 12:11 — Claude *(~35 s: from files already read)*

| Service | Route | Notes |
|---|---|---|
| booking | `POST /api/v1/bookings` | creates booking, reserves capacity downstream |
| booking | `GET /api/v1/bookings/{id}` | user-scoped via `X-User-ID` |
| booking | `GET /api/v1/bookings` | user-scoped list |
| booking | `GET /health` | |
| capacity | `POST /internal/v1/reservations` | decrements `available_capacity` |
| capacity | `GET /internal/v1/reservations?operation_id=` | **dedup lookup that exists and is never called** |
| capacity | `GET /internal/v1/slots/{id}` | returns `available_capacity`, `reservation_count` |
| capacity | `GET /health` | |

No `DELETE`, `PATCH` or `PUT` anywhere in either service.

`legacy-mobile/` is not a service — it's a Python model of the deployed client's
contract, used to check v1 responses still parse.

---

### 12:12 — Me

> List the identified issues.

### 12:13 — Claude *(~50 s)*

From the README backlog, plus what reading turned up:

| ID | Issue | Evidence |
|---|---|---|
| INC-1 | Duplicate booking on retry | `Idempotency-Key` is read (`bookings.py:22`) and stored (`:40`) but **never queried**; no unique constraint |
| **BUG-1** | **Slots read full with fewer bookings than capacity** | `available_capacity` decremented at `reservations.py:32`, incremented **nowhere**; `get_slot` counts all reservations ignoring `status` (`:69-71`) |
| FEAT-2 | Two users both get the last seat | check-then-act: reads capacity `:22`, decrements `:32`, no row lock |
| FEAT-1 | Rename `scheduled` → `confirmed` | **trap** — `legacy-mobile/client.py:32` is `Literal["scheduled","completed"]`, `strict=True`, and its tests *assert* `"confirmed"` raises. The rename breaks every deployed phone. Needs v2 or a translation layer |
| TECH-1 | Tests pass but miss the incident | `conftest.py` autouse-mocks `httpx.post` to always 201; `test_list_bookings` varies the key per iteration, so no test ever sends the same key twice |
| DESIGN-1 | Guarantees on downstream timeout | none today — no explicit timeout (httpx's implicit 5s), no retry budget, no compensation |

Two more not in the backlog:

- **The test suites `drop_all()` against the services' own databases**
  (`conftest.py`, autouse). `make test` destroys live data.
- `scripts/doctor.sh:34` checks port `15432`; compose publishes `25432`.

---

### 12:14 — Me

> Investigate BUG-1 and give me curl commands so I can reproduce it locally.

### 12:19 — Claude *(~4.5 min: `docker compose up --build` from cold, then verify)*

**Root cause — two, each independently sufficient:**

1. **No release path exists.** `available_capacity` is decremented at
   `reservations.py:32` and incremented nowhere — confirmed by grep across both
   services. Since `booking-api` reserves *before* committing its booking
   (`bookings.py:30-44`), any failure in that window strands the seat forever.
2. **`status` never transitions and the count ignores it.** Only ever written as
   `"reserved"` (`:29`); `get_slot` counts without filtering (`:69-71`).

**Reproduce** — run against `main`, before any fix:

```bash
make reset   # clean slate; create_all() won't alter existing tables
```

```bash
# 1. Berlin has exactly one seat
curl -s http://localhost:18081/internal/v1/slots/slot-berlin-0900 | python3 -m json.tool
```
```json
{ "total_capacity": 1, "available_capacity": 1, "reservation_count": 0 }
```

```bash
# 2. Simulate booking-api reserving, then dying before it commits
curl -s -X POST http://localhost:18081/internal/v1/reservations \
  -H "Content-Type: application/json" \
  -d '{"operation_id":"orphaned-op-001","slot_id":"slot-berlin-0900"}'
```

```bash
# 3. The seat is gone
curl -s http://localhost:18081/internal/v1/slots/slot-berlin-0900 | python3 -m json.tool
```
```json
{ "total_capacity": 1, "available_capacity": 0, "reservation_count": 1 }
```

```bash
# 4. ...but nobody holds a booking
curl -s http://localhost:18080/api/v1/bookings -H "X-User-ID: orphan-check"
```
```json
[]
```

```bash
# 5. And a real user is now refused
curl -s -X POST http://localhost:18080/api/v1/bookings \
  -H "Content-Type: application/json" \
  -H "X-User-ID: real-user" -H "Idempotency-Key: real-key-1" \
  -d '{"slot_id":"slot-berlin-0900"}' -w "\nHTTP %{http_code}\n"
```
```json
{"detail":"Failed to process booking"}
HTTP 500
```

That last line is a second finding: a full slot surfaces as **500**, not 409, so
the client is told to retry something that cannot succeed.

---

### 12:20 — Me

> Do we use a transaction anywhere in this flow?

### 12:23 — Claude *(~2.5 min)*

Within each service, yes. `capacity-api` inserts the reservation and decrements
the slot in one `db.commit()` — those two are atomic. `booking-api` commits its
booking row the same way.

**Across the two, no — and there's nowhere to put one.** Separate databases,
separate connections, no distributed transaction. That's not an oversight in
the code; it's the shape of the problem:

```
booking-api                     capacity-api
  ├─ reserve ─────────────────▶  INSERT reservation   [txn A, capacity_db]
  ├─ INSERT booking              [txn B, booking_db]
  └─ confirm ─────────────────▶  UPDATE reservation   [txn C, capacity_db]
```

Three transactions, two databases. Any gap between them is a window where the
system is inconsistent, and no `BEGIN` spans them.

So the question isn't "add a transaction" — it's **which side of the gap the
failure falls on**, and how the loser gets reconciled:

- Fail between reserve and the booking commit → seat held, no booking. Needs
  release or expiry to reclaim it. *This is BUG-1.*
- Fail between the booking commit and confirm → booking exists, seat still
  expiring. Worse: the seat lapses while a real booking lives, and the next
  user takes it. **That's an oversell.**

The second one is what I'd otherwise be introducing by fixing the first, so the
fix has to cover both. The transaction we *can* use is a local one: write the
booking **and a durable record of the intent to confirm** in the same
`booking_db` transaction. Either both land or neither does, and the intent
survives a crash to be retried. That's the outbox pattern, and it's what turns
confirm from best-effort into guaranteed.

---

### 12:25 — Me

> I reproduced it. Propose a fix — what are you going to change?

### 12:27 — Claude *(~1.5 min)*

**Stop storing availability; derive it.**

```
held      = status='confirmed' OR (status='reserved' AND expires_at > now())
available = total_capacity - count(held)
```

The bug exists *because* a counter and its rows are two sources of truth that
can silently disagree. A careful `+= 1` on release leaves that structure intact
and the next code path that frees a seat can forget again. Deriving makes drift
impossible rather than merely fixed — and expiry then falls out free, which
matters because this repo has no job runner to host a sweeper.

Concretely:

1. **`capacity-api/app/models.py`** — `status` becomes `reserved → confirmed |
   released`; add `expires_at` and a 15-minute TTL; index `(slot_id, status)`;
   one `held_filter()` helper so the availability check and `reservation_count`
   can't drift apart. Keep `"reserved"` as the initial value — an existing test
   asserts it and renaming buys nothing.
2. **`capacity-api/.../reservations.py`** — derive availability; stop writing
   `slot.available_capacity`; add `POST .../{operation_id}/release` and
   `.../confirm`.
3. **`booking-api/.../bookings.py`** — confirm after the booking commits;
   release in a `finally` guard if we reserved but never committed; map a
   downstream 409 to **409** instead of 500.
4. **`booking-api/app/models.py`** — an `outbox` table. The booking and the
   intent to confirm it commit in one local transaction, so the confirm cannot
   be lost with the booking durable. Retried until capacity-api accepts.

**Release is a fast path, not the mechanism of correctness.** It's best-effort
and logged, never raised — the failure it compensates for is the same kind that
can eat the compensation. Expiry is what actually guarantees the seat comes
back.

Not touching: INC-1, FEAT-2's oversell race, FEAT-1's rename trap, or
backfilling seats already leaked.

Spec first, then a failing test, then the change.

---

### 12:29 — Me

> Write the spec first.

### 12:34 — Claude *(~4.5 min)*

`docs/specs/bug-1-capacity-release.spec.md` — seven acceptance criteria, numbered
so they become test names:

| | |
|---|---|
| **AC-1** | A reservation whose booking never completes stops holding a seat |
| **AC-2** | Releasing is idempotent; capacity is not credited twice |
| **AC-3** | `available_capacity <= total_capacity` always |
| **AC-4** | A completed booking keeps its seat indefinitely |
| **AC-5** | `reservation_count` reflects seats held, not rows |
| **AC-6** | Releasing an unknown `operation_id` is not an error |
| **AC-7** | A full slot reports 409, not 500 |
| **AC-8** | A committed booking's seat is not lost to a failed confirm |

AC-8 is the one your transaction question produced. Fixing AC-1 naively would
have introduced it: reclaiming stranded seats means a TTL, and a TTL plus a
best-effort confirm means a committed booking can lose its seat. Worth an AC of
its own rather than a line in the implementation.

Out of scope, written down: INC-1, FEAT-2, FEAT-1's rename, backfilling leaked
seats.

Open question flagged rather than silently chosen: **how long may an unconfirmed
reservation hold a seat?** Assuming 15 minutes; needs a product answer.

Plus `docs/adr/0001-capacity-accounting.md` for the contested call — store the
counter vs derive it. Chose derive: one source of truth, and expiry then needs
no sweeper.

---

### 12:35 — Me

> 15 minutes is OK. Now the failing tests.

### 12:39 — Claude *(~3.5 min)*

One test per acceptance criterion:

| Test | AC |
|---|---|
| `test_released_reservation_frees_capacity` | AC-1 |
| `test_release_is_idempotent` | AC-2 |
| `test_lapsed_reservation_frees_capacity_without_a_sweeper` | AC-3 |
| `test_confirmed_reservation_never_lapses` | AC-4 |
| `test_release_does_not_strip_a_confirmed_seat` | AC-5 |
| `test_release_unknown_operation_succeeds` | AC-6 |
| `test_full_slot_returns_409` | AC-7 |
| `test_failed_confirm_leaves_the_intent_durable` | AC-8 |

Red, and for the right reason:

```
/app/tests/test_reservations.py:30: assert 404 == 200   ← no release route yet
/app/tests/test_bookings.py:29:     assert 500 == 409   ← error still flattened
```

Committed red, separately from the fix.

---

### 12:40 — Me

> Go ahead with the fix.

### 12:50 — Claude *(~10 min: two services plus the outbox, rebuild between)*

**`capacity-api/app/models.py`** — state machine plus one shared definition:

```python
RESERVATION_TTL = timedelta(minutes=15)
STATUS_RESERVED, STATUS_CONFIRMED, STATUS_RELEASED = "reserved", "confirmed", "released"

def held_filter():
    """Confirmed, or reserved and not yet lapsed."""
    return or_(
        Reservation.status == STATUS_CONFIRMED,
        and_(Reservation.status == STATUS_RESERVED,
             Reservation.expires_at > func.now()),
    )
```

`held_filter()` is used by *both* the availability check and
`reservation_count`, so they cannot drift apart — the two-sources-of-truth
problem is what caused the bug, and one helper is the structural answer.
`func.now()` not Python's clock: one clock, no skew between services.

Added `expires_at`, and an index on `(slot_id, status)` since availability is
now a `COUNT`. Kept `"reserved"` as the initial value — renaming it to
`"pending"` broke an existing contract test and bought nothing, so I reverted
that.

**`capacity-api/.../reservations.py`** — availability derived, never stored:

```python
if slot.total_capacity - _held_count(db, body.slot_id) <= 0:
    raise HTTPException(status_code=409, detail="No capacity available")
```

Plus `POST .../{operation_id}/release` and `.../confirm`. Release is forgiving
(unknown → 200, double-release → 200); confirm is strict (unknown → 404,
after-release → **409**, since a seat given away can't be silently retaken).

**`booking-api/.../bookings.py`** — drives the lifecycle:

```python
_confirm_reservation(booking_id)   # after the booking row commits
...
finally:
    if reserved:                   # we took a seat and never confirmed it
        _release_reservation(booking_id)
```

Release stays best-effort and logged — expiry is its backstop, and the user
must not be told their booking failed because a compensation did. And a
downstream 409 now maps to 409 instead of 500.

**Confirm is not left best-effort** — that was the gap the transaction question
exposed. The booking and the intent to confirm it commit together:

```python
db.add(booking)
db.add(OutboxEntry(operation_id=booking_id, action=ACTION_CONFIRM_RESERVATION))
db.commit()          # one transaction, booking_db — both rows or neither
```

Confirm is attempted immediately and the outbox row deleted only once
capacity-api accepts. If it fails, the row stays, `attempts` increments, and a
later request retries it. Draining is opportunistic (bounded to 5 entries per
request) rather than scheduled — no job runner here, and piggybacking on
request traffic needs no new infrastructure. A real deployment would run it as
a worker so recovery doesn't depend on the next booking arriving.

---

### 12:51 — Me

> Verify it.

### 12:59 — Claude *(~7.5 min: `make reset`, full gate, live checks)*

Tests green, but the ones that matter are live — a passing mock proves less
than a real seat coming back:

```
release:   available=19 held=1  →  release HTTP 200  →  available=20 held=0
expiry:    available=19         →  (backdate expires_at)  →  available=20
confirm:   POST /api/v1/bookings → 201, reservation status = confirmed
full slot: second user → HTTP 409  {"detail":"No capacity available"}   (was 500)
```

The expiry check is the one I'd lead with: I backdated `expires_at` directly in
Postgres and the seat returned **with no sweeper having run**. That's the claim
ADR 0001 makes, tested rather than asserted.

| Check | Result |
|---|---|
| Release frees the seat | 19 → 20, live |
| Expiry frees it, no sweeper | 19 → 20, live |
| Confirmed seat never lapses | `expires_at` a year past, still held |
| Failed commit releases | unit test patching `Session.commit` |
| Full slot → client | **409**, live |
| Confirm survives a crash | outbox row persists, next request settles it |
| capacity-api / booking-api / legacy-mobile | 14 / 13 / 6 passed |
| ruff, mypy | clean |

`legacy-mobile` passing matters independently: it proves the v1 response shape
is untouched, which is the trap FEAT-1 sets.

**Stated plainly, not buried:** `make reproduce` still reports the INC-1
duplicate on this branch. That's expected — this change fixes capacity release
only, and the plan says not to read it as a failure. FEAT-2's oversell race is
also untouched: availability is derived now, but the check and the insert still
aren't serialised.

`make reset` is mandatory — `create_all()` won't add `expires_at` or the index
to an existing table. That's the same missing-migration-tool gap, surfacing a
second time.

Built as five steps — docs → red tests → capacity-api → booking-api → docs —
then squashed to one commit for the PR. The outbox went into the booking-api
step, not a follow-up: it is part of making the lifecycle correct, not an
afterthought.

---

### 13:00 — Me

> Give me curl commands to check the fixed version manually, and walk me
> through what gets written to which table during a reservation.

### 13:04 — Claude *(~4 min)*

**Manual check — run on `fix/bug-1-standalone`:**

```bash
make reset   # mandatory: create_all() won't add expires_at to an existing table
```

```bash
# 1. Berlin: one seat, free
curl -s http://localhost:18081/internal/v1/slots/slot-berlin-0900
```
```json
{"total_capacity":1,"available_capacity":1,"reservation_count":0}
```

```bash
# 2. Orphan a reservation (booking-api reserved, then died)
curl -s -X POST http://localhost:18081/internal/v1/reservations \
  -H "Content-Type: application/json" \
  -d '{"operation_id":"manual-1","slot_id":"slot-berlin-0900"}'
```
```json
{"operation_id":"manual-1","slot_id":"slot-berlin-0900","status":"reserved"}
```

```bash
# 3. Seat is held
curl -s http://localhost:18081/internal/v1/slots/slot-berlin-0900
```
```json
{"total_capacity":1,"available_capacity":0,"reservation_count":1}
```

```bash
# 4. Release it  ← this endpoint did not exist before
curl -s -X POST http://localhost:18081/internal/v1/reservations/manual-1/release
```
```json
{"operation_id":"manual-1","slot_id":"slot-berlin-0900","status":"released"}
```

```bash
# 5. Seat is back — this is the bug, fixed
curl -s http://localhost:18081/internal/v1/slots/slot-berlin-0900
```
```json
{"total_capacity":1,"available_capacity":1,"reservation_count":0}
```

```bash
# 6. Release again — idempotent, no double credit (AC-2)
curl -s -X POST http://localhost:18081/internal/v1/reservations/manual-1/release \
  -o /dev/null -w "HTTP %{http_code}\n"
curl -s http://localhost:18081/internal/v1/slots/slot-berlin-0900
```
```
HTTP 200
{"total_capacity":1,"available_capacity":1,"reservation_count":0}
```

Still 1, not 2. A counter-based fix is exactly where that would have gone wrong.

```bash
# 7. Full slot now returns 409, not 500 (AC-7)
curl -s -X POST http://localhost:18080/api/v1/bookings \
  -H "Content-Type: application/json" \
  -H "X-User-ID: carol" -H "Idempotency-Key: carol-1" \
  -d '{"slot_id":"slot-berlin-0900"}'          # 201, takes the seat
curl -s -X POST http://localhost:18080/api/v1/bookings \
  -H "Content-Type: application/json" \
  -H "X-User-ID: dave" -H "Idempotency-Key: dave-1" \
  -d '{"slot_id":"slot-berlin-0900"}' -w "\nHTTP %{http_code}\n"
```
```json
{"detail":"No capacity available"}
HTTP 409
```

```bash
# 8. Expiry, with no sweeper running
docker compose exec -T postgres psql -U challenge -d capacity_db -c \
  "UPDATE reservations SET expires_at = now() - interval '1 minute'
   WHERE operation_id = 'manual-1';"
curl -s http://localhost:18081/internal/v1/slots/slot-berlin-0900
```

---

**What gets written where.** Two databases, no shared transaction — which is
the whole reason this is hard.

| # | Step | `booking_db` | `capacity_db.reservations` | `capacity_db.slots` |
|---|---|---|---|---|
| 1 | `POST /api/v1/bookings` arrives | — | — | — |
| 2 | booking-api calls capacity-api | — | **INSERT** `status='reserved'`, `expires_at=now()+15min` | untouched |
| 3 | booking-api commits | **INSERT** booking + **INSERT** `outbox` intent — *one transaction* | — | untouched |
| 4 | booking-api confirms | **DELETE** the outbox row | **UPDATE** `status='confirmed'`, `expires_at=NULL` | untouched |

**`slots` is never written.** That column is the fix: availability is a `COUNT`
over `reservations`, so there is no counter to get out of step.

Failure between 2 and 3 is the bug's whole story:

| Failure point | State left behind | How the seat comes back |
|---|---|---|
| After 2, handled | reserved row, no booking | `finally` → release, **immediately** |
| After 2, process dies | reserved row, no booking | `expires_at` lapses, **≤15 min** |
| After 3, before 4 | booking + reserved row + **outbox intent** | next request drains the outbox and confirms |
| After 4 | booking + confirmed row | never — confirmed rows don't expire |

Row 3 was the weak spot before the outbox: a lost confirm meant a real
booking's seat expiring, and the next user taking it — an oversell. Committing
the intent alongside the booking closes it, because the instruction to finish
the job is as durable as the booking itself.

What's still true, and I'd raise it unprompted: draining is opportunistic, so
recovery waits for the next booking to arrive. On a quiet slot that could
exceed the 15-minute TTL. A worker on a timer fixes it and is a few lines —
left out only because this repo has no runner to host one. And if a seat has
already been taken by the time the intent is retried, no mechanism can reconcile
it; that needs a product decision about overbooking, not more code.

**Proof the derived model is doing the work** — after the booking above:

```
capacity_db.slots:  available_capacity = 1     ← stale, vestigial column
GET /internal/v1/slots/...: "available_capacity": 0   ← derived, correct
```

The old column still says the seat is free. Nothing reads it any more, which is
precisely why the bug can't recur.

---

*Continues from here.*
