from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

ACTION_CONFIRM_RESERVATION = "confirm_reservation"


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    slot_id: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="scheduled")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OutboxEntry(Base):
    """A durable intent to call capacity-api, written with the booking itself.

    The booking lives in booking_db and the reservation in capacity_db, so no
    transaction can span them. Writing this row in the *same* transaction as the
    booking is what makes the confirm survive a crash: either both land or
    neither does, and the row is the instruction to finish the job.
    """

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # One outstanding action per booking; also makes the drain idempotent.
    operation_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
