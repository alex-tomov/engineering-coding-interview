# Appointment Booking System

An appointment booking system with two backend services and a legacy mobile client.

> This repository contains more work than anyone is expected to finish
> during the interview. We assess investigation, prioritization,
> communication, judgment, and validation -- not the number of completed
> tasks.

## Quick Start

```bash
make doctor     # Check prerequisites
make up         # Build and start all services
make test       # Run baseline tests
make reproduce  # Demonstrate the reported incident
```

Requires Docker and Docker Compose. No local Python installation needed.

## System Overview

```
Legacy mobile client
        |
        | HTTP API v1
        v
+-------------------+
|    booking-api    |  :18080

| FastAPI / PG      |
+-------------------+
        |
        | Internal HTTP API
        v
+-------------------+
|   capacity-api    |  :18081
| FastAPI / PG      |
+-------------------+
```

- **booking-api** -- public API consumed by the mobile app. Creates bookings and delegates capacity management downstream.
- **capacity-api** -- internal service. Manages appointment slot capacity and reservations.
- **legacy-mobile** -- Python model of the deployed mobile client. Parses API v1 responses through strict schemas.

Each service owns a separate PostgreSQL database. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## The Incident

A production incident has been reported:

> A user submitted an appointment booking and received a timeout. They
> tapped Retry and later saw two bookings for the same slot. The slot
> capacity was also reduced twice.

Investigate the incident and make the safest improvement you can. You may
change code and tests. Please explain important assumptions and ask for
missing requirements.

Run `make reproduce` to see the incident in action.

## Backlog

Start with the incident, then choose from the backlog based on your
judgment.

| ID | Type | Summary |
|----|------|---------|
| INC-1 | Incident | Duplicate booking on retry (start here) |
| BUG-1 | Bug | Slots showing as full when fewer bookings than capacity exist |
| FEAT-1 | Feature | Add cancellation support; product wants to rename "scheduled" to "confirmed" |
| FEAT-2 | Bug | Two users simultaneously booked the last available slot -- both succeeded |
| TECH-1 | Tech debt | Existing tests pass but may not catch the reported incident |
| DESIGN-1 | Discussion | What guarantees does the system provide when a downstream call times out? |

## Commands Reference

| Command | Description |
|---------|-------------|
| `make doctor` | Verify Docker and port availability |
| `make up` | Build and start services with health checks |
| `make down` | Stop all services |
| `make test` | Run baseline test suites (< 30s) |
| `make reproduce` | Reproduce the duplicate booking incident |
| `make reset` | Full reset: remove volumes, rebuild, restart |
| `make logs` | Tail service logs |

Equivalent Docker Compose commands:

```bash
docker compose up -d --build          # Start
docker compose down                    # Stop
docker compose down -v                 # Stop and remove volumes
docker compose exec booking-api pytest tests/ -v   # Run booking tests
docker compose exec capacity-api pytest tests/ -v  # Run capacity tests
docker compose logs -f booking-api     # Tail one service
```

## Notes

- You may use any AI tools you normally use.
- Explain important assumptions.
- Ask for missing requirements rather than guessing.
- The `legacy-mobile/` directory contains the deployed mobile client contract.
- Focus on making the system better in the area you are working on. A broad rewrite is not expected.
