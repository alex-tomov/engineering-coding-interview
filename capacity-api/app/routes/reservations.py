from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Reservation, Slot
from app.schemas import ReservationCreate, ReservationResponse, SlotResponse

router = APIRouter()


def _to_response(reservation: Reservation) -> ReservationResponse:
    return ReservationResponse(
        reservation_id=reservation.id,
        operation_id=reservation.operation_id,
        slot_id=reservation.slot_id,
        status=reservation.status,
    )


@router.post("/internal/v1/reservations", status_code=201)
def create_reservation(
    body: ReservationCreate, db: Session = Depends(get_db)
) -> ReservationResponse:
    # Replay an earlier reservation for the same operation instead of
    # consuming capacity again.
    existing = (
        db.query(Reservation)
        .filter(Reservation.operation_id == body.operation_id)
        .first()
    )
    if existing:
        return _to_response(existing)

    slot = db.query(Slot).filter(Slot.id == body.slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    if slot.available_capacity <= 0:
        raise HTTPException(status_code=409, detail="No capacity available")

    reservation = Reservation(
        id=str(uuid4()),
        operation_id=body.operation_id,
        slot_id=body.slot_id,
        status="reserved",
    )
    db.add(reservation)
    slot.available_capacity -= 1
    try:
        db.commit()
    except IntegrityError:
        # A concurrent call for the same operation won the race; the lookup
        # above is check-then-act, and the unique constraint closes it.
        db.rollback()
        duplicate = (
            db.query(Reservation)
            .filter(Reservation.operation_id == body.operation_id)
            .first()
        )
        if duplicate:
            return _to_response(duplicate)
        raise
    db.refresh(reservation)

    return _to_response(reservation)


@router.get("/internal/v1/reservations")
def list_reservations(
    operation_id: Optional[str] = None, db: Session = Depends(get_db)
) -> list[ReservationResponse]:
    query = db.query(Reservation)
    if operation_id:
        query = query.filter(Reservation.operation_id == operation_id)
    reservations = query.all()
    return [
        ReservationResponse(
            reservation_id=r.id,
            operation_id=r.operation_id,
            slot_id=r.slot_id,
            status=r.status,
        )
        for r in reservations
    ]


@router.get("/internal/v1/slots/{slot_id}")
def get_slot(slot_id: str, db: Session = Depends(get_db)) -> SlotResponse:
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    reservation_count = (
        db.query(Reservation).filter(Reservation.slot_id == slot_id).count()
    )

    return SlotResponse(
        id=slot.id,
        location=slot.location,
        time=slot.time,
        total_capacity=slot.total_capacity,
        available_capacity=slot.available_capacity,
        reservation_count=reservation_count,
    )
