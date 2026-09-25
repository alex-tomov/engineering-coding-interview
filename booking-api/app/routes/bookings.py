import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import ACTION_CONFIRM_RESERVATION, Booking, OutboxEntry
from app.schemas import CreateBookingRequest

router = APIRouter()

logger = logging.getLogger(__name__)

# How many pending outbox entries to retry per request. Bounded so a backlog
# cannot turn one user's booking into an unbounded catch-up job.
OUTBOX_DRAIN_BATCH = 5


def _confirm_reservation(operation_id: str) -> bool:
    """Pin the seat. Returns whether capacity-api accepted it.

    Never raises: the booking exists and the user must not be told otherwise by
    a failure here. An unsuccessful call leaves the outbox row in place, so the
    intent survives and is retried.
    """
    try:
        httpx.post(
            f"{settings.capacity_api_url}"
            f"/internal/v1/reservations/{operation_id}/confirm"
        ).raise_for_status()
        return True
    except Exception:
        logger.exception("Failed to confirm reservation %s", operation_id)
        return False


def _settle_outbox_entry(db: Session, entry: OutboxEntry) -> bool:
    """Do the work the entry describes, and delete it only once it succeeded."""
    if _confirm_reservation(entry.operation_id):
        db.delete(entry)
        db.commit()
        return True

    entry.attempts += 1
    db.commit()
    return False


def _drain_outbox(db: Session) -> None:
    """Retry intents left behind by earlier requests that died mid-flight.

    Opportunistic rather than scheduled: this repo has no job runner, and
    piggybacking on request traffic needs no new infrastructure. A real
    deployment would run this as a worker so recovery does not depend on the
    next booking arriving.
    """
    pending = (
        db.query(OutboxEntry)
        .filter(OutboxEntry.action == ACTION_CONFIRM_RESERVATION)
        .order_by(OutboxEntry.created_at)
        .limit(OUTBOX_DRAIN_BATCH)
        .all()
    )
    for entry in pending:
        _settle_outbox_entry(db, entry)


def _release_reservation(operation_id: str) -> None:
    """Give an unconfirmed seat back early. Best-effort; expiry is the backstop."""
    try:
        httpx.post(
            f"{settings.capacity_api_url}"
            f"/internal/v1/reservations/{operation_id}/release"
        ).raise_for_status()
    except Exception:
        logger.exception("Failed to release reservation %s", operation_id)


@router.post("/api/v1/bookings", status_code=201)
def create_booking(
    request: Request,
    body: CreateBookingRequest,
    db: Session = Depends(get_db),
):
    user_id = request.headers.get("X-User-ID")
    idempotency_key = request.headers.get("Idempotency-Key")

    if not user_id:
        raise HTTPException(status_code=400, detail="X-User-ID header required")

    # Finish anything an earlier request left half-done before starting ours.
    _drain_outbox(db)

    booking_id = str(uuid.uuid4())
    reserved = False

    try:
        resp = httpx.post(
            f"{settings.capacity_api_url}/internal/v1/reservations",
            json={"operation_id": booking_id, "slot_id": body.slot_id},
        )
        resp.raise_for_status()
        reserved = True

        booking = Booking(
            id=booking_id,
            user_id=user_id,
            slot_id=body.slot_id,
            idempotency_key=idempotency_key,
            status="scheduled",
        )
        # One transaction, two rows: the booking and the durable intent to
        # confirm its seat. booking_db and capacity_db cannot share a
        # transaction, so this is what makes the confirm survive a crash --
        # either both rows land or neither does.
        db.add(booking)
        db.add(
            OutboxEntry(
                operation_id=booking_id,
                action=ACTION_CONFIRM_RESERVATION,
            )
        )
        db.commit()
        db.refresh(booking)

        # The seat is ours; try to pin it now. If this fails the outbox row
        # remains and a later request retries it.
        outbox_entry = (
            db.query(OutboxEntry)
            .filter(OutboxEntry.operation_id == booking_id)
            .first()
        )
        if outbox_entry:
            _settle_outbox_entry(db, outbox_entry)
        reserved = False

        if settings.challenge_mode:
            fault = request.headers.get("X-Challenge-Fault")
            if fault == "post-commit":
                raise Exception("Simulated post-commit failure")

        return {
            "id": booking.id,
            "slot_id": booking.slot_id,
            "status": booking.status,
            "created_at": booking.created_at.isoformat(),
        }

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            # The slot is full. That is the caller's situation, not a fault on
            # our side, and a 500 tells the client to retry something that
            # cannot succeed.
            raise HTTPException(status_code=409, detail="No capacity available")
        raise HTTPException(status_code=500, detail="Failed to process booking")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        # We took a seat and never confirmed it. Hand it back now rather than
        # making the next user wait out the TTL. Best-effort by design: if this
        # call is lost, expiry still reclaims the seat.
        if reserved:
            _release_reservation(booking_id)


@router.get("/api/v1/bookings/{booking_id}")
def get_booking(booking_id: str, request: Request, db: Session = Depends(get_db)):
    user_id = request.headers.get("X-User-ID")
    if not user_id:
        raise HTTPException(status_code=400, detail="X-User-ID header required")

    booking = (
        db.query(Booking)
        .filter(Booking.id == booking_id, Booking.user_id == user_id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    return {
        "id": booking.id,
        "slot_id": booking.slot_id,
        "status": booking.status,
        "created_at": booking.created_at.isoformat(),
    }


@router.get("/api/v1/bookings")
def list_bookings(request: Request, db: Session = Depends(get_db)):
    user_id = request.headers.get("X-User-ID")
    if not user_id:
        raise HTTPException(status_code=400, detail="X-User-ID header required")

    bookings = db.query(Booking).filter(Booking.user_id == user_id).all()

    return [
        {
            "id": b.id,
            "slot_id": b.slot_id,
            "status": b.status,
            "created_at": b.created_at.isoformat(),
        }
        for b in bookings
    ]
