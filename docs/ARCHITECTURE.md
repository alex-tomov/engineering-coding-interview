# Appointment Booking System — Architecture

Last updated: 2024-06-14

## Overview

The appointment booking system allows patients to book limited-capacity
appointment slots through a mobile application. The backend consists of two
services:

- **booking-api**: public-facing API consumed by the mobile application.
- **capacity-api**: internal service that manages slot capacity and
  reservations.

Each service owns its own PostgreSQL database. There is no shared database.

## Booking Flow

1. The mobile client sends a `POST /api/v1/bookings` request with an
   `Idempotency-Key` header and a `slot_id`.
2. `booking-api` calls `capacity-api` to create a capacity reservation.
3. `capacity-api` creates a reservation record and decrements the slot's
   available capacity.
4. `booking-api` creates a booking record and returns it to the client.

## Data Ownership

| Entity       | Service      | Database     |
|-------------|-------------|-------------|
| Bookings     | booking-api  | booking_db   |
| Slots        | capacity-api | capacity_db  |
| Reservations | capacity-api | capacity_db  |

## Reservation Lifecycle

A seat moves through three states, owned by `capacity-api`:

```
   reserved ──confirm──▶ confirmed     (permanent; never expires)
      │
      ├──release──▶ released           (seat returned immediately)
      │
      └──expires_at elapses──▶ (no longer counted)
```

A reservation **holds a seat** iff it is `confirmed`, or `reserved` with
`expires_at` still in the future. Availability is computed from that, never
stored:

```
available_capacity = total_capacity - count(held reservations for the slot)
```

`booking-api` drives the transitions: it reserves, commits its booking, then
confirms. If it reserved but never committed, it releases in a `finally` guard.
That release is a **fast path, not the mechanism of correctness** — an
unconfirmed reservation lapses on its own after `RESERVATION_TTL`
(15 minutes), so a lost release call cannot leak a seat. There is no sweeper
and none is needed: an expired row simply stops being counted. See
[ADR 0001](adr/0001-capacity-accounting.md).

`Slot.available_capacity` still exists as a column but is no longer written or
read. `create_all()` cannot drop a column.

### Why confirm is durable and release is not

The two compensating calls are deliberately asymmetric. A lost **release** costs
nothing — expiry reclaims the seat anyway. A lost **confirm** is different: the
booking is committed, so its seat expires underneath a real appointment and the
next user takes it. That is an oversell.

`booking_db` and `capacity_db` cannot share a transaction, so `booking-api`
writes the booking **and a record of the intent to confirm** in one local
`booking_db` transaction:

| | `booking_db` | `capacity_db.reservations` |
|---|---|---|
| reserve | — | INSERT `reserved`, `expires_at = now() + TTL` |
| commit | INSERT booking **+** INSERT `outbox` intent *(one transaction)* | — |
| confirm | DELETE the outbox row | UPDATE `confirmed`, `expires_at = NULL` |

Either both rows land or neither does, so the instruction to finish the job is
as durable as the booking itself. Confirm is retried until `capacity-api`
accepts; being idempotent, at-least-once delivery is sufficient.

Draining the outbox is **opportunistic** — each booking request settles a few
outstanding intents — because there is no job runner in this repo. A real
deployment should run the drain as a worker, so recovery does not wait for the
next booking to arrive.

## Schema Management

Both services create their schema with `Base.metadata.create_all()` at startup.
This creates missing tables but **does not alter existing ones** — `expires_at`
and the `(slot_id, status)` index will not appear on a database that already
has the `reservations` table. Locally, `make reset` (which drops volumes) is
required. A production deployment needs a migration tool; this repository has
none.

## Current Assumptions

> **Architecture note (2024-06-14):** The current implementation assumes
> low request volume. Idempotency handling and concurrent-booking protection
> were deferred to a future sprint. The `idempotency_key` field is stored
> for future use but is not currently enforced.

> **Still true as of 2026-09-25.** This change fixes capacity *release*
> (BUG-1) and does not touch idempotency: a retry still creates a second
> booking and a second reservation (INC-1). `capacity-api` also still checks
> availability and inserts without a row lock, so two concurrent requests can
> both take the last seat (FEAT-2).

## API Versioning

The mobile client consumes API v1. The response contract is documented in
the `legacy-mobile/` directory, which contains a Python model of the
deployed client's expectations.

Any changes to the v1 response format should be validated against the
legacy client before deployment.

## Error Handling

Errors from downstream services are currently mapped to HTTP 500 responses.
A future improvement would provide more specific error codes to the mobile
client.
