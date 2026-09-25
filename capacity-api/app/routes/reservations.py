from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    RESERVATION_TTL,
    STATUS_CONFIRMED,
    STATUS_RESERVED,
    STATUS_RELEASED,
    Reservation,
    Slot,
    held_filter,
)
from app.schemas import ReservationCreate, ReservationResponse, SlotResponse

router = APIRouter()


def _to_response(reservation: Reservation) -> ReservationResponse:
    return ReservationResponse(
        reservation_id=reservation.id,
        operation_id=reservation.operation_id,
        slot_id=reservation.slot_id,
        status=reservation.status,
    )


def _held_count(db: Session, slot_id: str) -> int:
    return (
        db.query(Reservation)
        .filter(Reservation.slot_id == slot_id)
        .filter(held_filter())
        .count()
    )


def _by_operation(db: Session, operation_id: str) -> Optional[Reservation]:
    return (
        db.query(Reservation)
        .filter(Reservation.operation_id == operation_id)
        .first()
    )


@router.post("/internal/v1/reservations", status_code=201)
def create_reservation(
    body: ReservationCreate, db: Session = Depends(get_db)
) -> ReservationResponse:
    slot = db.query(Slot).filter(Slot.id == body.slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    # Derived, never stored -- so the count and the rows cannot disagree.
    if slot.total_capacity - _held_count(db, body.slot_id) <= 0:
        raise HTTPException(status_code=409, detail="No capacity available")

    reservation = Reservation(
        id=str(uuid4()),
        operation_id=body.operation_id,
        slot_id=body.slot_id,
        status=STATUS_RESERVED,
        expires_at=func.now() + RESERVATION_TTL,
    )
    db.add(reservation)
    db.commit()
    db.refresh(reservation)

    return _to_response(reservation)


@router.post("/internal/v1/reservations/{operation_id}/confirm")
def confirm_reservation(
    operation_id: str, db: Session = Depends(get_db)
) -> ReservationResponse:
    """Hold the seat permanently. Confirmed reservations never lapse."""
    reservation = _by_operation(db, operation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")

    if reservation.status == STATUS_RELEASED:
        # The seat has already been given back and may belong to someone else
        # by now. The caller must reserve afresh rather than silently retake it.
        raise HTTPException(
            status_code=409, detail="Reservation was already released"
        )

    if reservation.status != STATUS_CONFIRMED:
        reservation.status = STATUS_CONFIRMED
        reservation.expires_at = None
        db.commit()
        db.refresh(reservation)

    return _to_response(reservation)


@router.post("/internal/v1/reservations/{operation_id}/release")
def release_reservation(operation_id: str, db: Session = Depends(get_db)) -> dict:
    """Give the seat back immediately.

    This is a fast path, not the mechanism of correctness -- an unconfirmed
    reservation lapses on its own, so a lost release call cannot leak a seat.
    Deliberately forgiving: a compensating call must never fail because its
    subject is already gone (spec AC-6).
    """
    reservation = _by_operation(db, operation_id)
    if not reservation:
        return {"operation_id": operation_id, "status": STATUS_RELEASED}

    if reservation.status == STATUS_CONFIRMED:
        # Reclaiming a confirmed seat would strip a real booking.
        return _to_response(reservation).model_dump()

    if reservation.status != STATUS_RELEASED:
        reservation.status = STATUS_RELEASED
        reservation.expires_at = None
        db.commit()
        db.refresh(reservation)

    return _to_response(reservation).model_dump()


@router.get("/internal/v1/reservations")
def list_reservations(
    operation_id: Optional[str] = None, db: Session = Depends(get_db)
) -> list[ReservationResponse]:
    query = db.query(Reservation)
    if operation_id:
        query = query.filter(Reservation.operation_id == operation_id)
    return [_to_response(r) for r in query.all()]


@router.get("/internal/v1/slots/{slot_id}")
def get_slot(slot_id: str, db: Session = Depends(get_db)) -> SlotResponse:
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    held = _held_count(db, slot_id)

    return SlotResponse(
        id=slot.id,
        location=slot.location,
        time=slot.time,
        total_capacity=slot.total_capacity,
        available_capacity=slot.total_capacity - held,
        reservation_count=held,
    )
