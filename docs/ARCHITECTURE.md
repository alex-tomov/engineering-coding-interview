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

## Current Assumptions

> **Architecture note (2026-09-24):** Idempotency is now enforced at both
> service edges — see [ADR 0001](adr/0001-idempotent-booking-creation.md).
> `booking-api` deduplicates on `(user_id, idempotency_key)`; `capacity-api`
> deduplicates on `operation_id`. Both are backed by unique constraints.
>
> Still outstanding: capacity is never *released*, so a reservation whose
> booking never committed holds a seat indefinitely (BUG-1); and
> `capacity-api` still does read-then-decrement without a row lock, so two
> distinct operations can both take the last seat (FEAT-2).

> **Superseded (2024-06-14):** The current implementation assumes low request
> volume. Idempotency handling and concurrent-booking protection were deferred
> to a future sprint. The `idempotency_key` field is stored for future use but
> is not currently enforced.

## Schema Management

Both services create their schema with `Base.metadata.create_all()` at startup.
This creates missing tables but **does not alter existing ones** — a newly added
constraint will not appear on a database that already has the table. Locally,
`make reset` (which drops volumes) is required. A production deployment needs a
migration tool; this repository has none.

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
