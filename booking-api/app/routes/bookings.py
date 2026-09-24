import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Booking
from app.schemas import CreateBookingRequest

router = APIRouter()

# Fixed namespace so a given (user, idempotency key) always maps to the same
# booking id -- and therefore the same downstream operation_id on a retry.
BOOKING_NAMESPACE = uuid.UUID("6f8a9c1e-4b3d-4f2a-9e71-2c5d8a0b7f43")


def _booking_id_for(user_id: str, idempotency_key: str | None) -> str:
    if idempotency_key:
        return str(uuid.uuid5(BOOKING_NAMESPACE, f"{user_id}:{idempotency_key}"))
    return str(uuid.uuid4())


def _serialize(booking: Booking) -> dict:
    return {
        "id": booking.id,
        "slot_id": booking.slot_id,
        "status": booking.status,
        "created_at": booking.created_at.isoformat(),
    }


def _find_existing(
    db: Session, user_id: str, idempotency_key: str | None
) -> Booking | None:
    if not idempotency_key:
        return None
    return (
        db.query(Booking)
        .filter(
            Booking.user_id == user_id,
            Booking.idempotency_key == idempotency_key,
        )
        .first()
    )


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

    # Replay the original result rather than booking again. This is what makes
    # the client's Retry safe.
    existing = _find_existing(db, user_id, idempotency_key)
    if existing:
        return _serialize(existing)

    booking_id = _booking_id_for(user_id, idempotency_key)

    try:
        resp = httpx.post(
            f"{settings.capacity_api_url}/internal/v1/reservations",
            json={"operation_id": booking_id, "slot_id": body.slot_id},
        )
        resp.raise_for_status()

        booking = Booking(
            id=booking_id,
            user_id=user_id,
            slot_id=body.slot_id,
            idempotency_key=idempotency_key,
            status="scheduled",
        )
        db.add(booking)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent request with the same key committed first. The
            # lookup above is check-then-act; this closes that window.
            db.rollback()
            duplicate = _find_existing(db, user_id, idempotency_key)
            if duplicate:
                return _serialize(duplicate)
            raise
        db.refresh(booking)

        if settings.challenge_mode:
            fault = request.headers.get("X-Challenge-Fault")
            if fault == "post-commit":
                raise Exception("Simulated post-commit failure")

        return _serialize(booking)

    except httpx.HTTPStatusError:
        raise HTTPException(status_code=500, detail="Failed to process booking")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


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

    return _serialize(booking)


@router.get("/api/v1/bookings")
def list_bookings(request: Request, db: Session = Depends(get_db)):
    user_id = request.headers.get("X-User-ID")
    if not user_id:
        raise HTTPException(status_code=400, detail="X-User-ID header required")

    bookings = db.query(Booking).filter(Booking.user_id == user_id).all()

    return [_serialize(b) for b in bookings]
