import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Booking
from app.schemas import CreateBookingRequest

router = APIRouter()


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

    booking_id = str(uuid.uuid4())

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
        db.commit()
        db.refresh(booking)

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
