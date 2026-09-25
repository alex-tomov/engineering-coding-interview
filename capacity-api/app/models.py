import uuid
from datetime import datetime, timedelta

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    and_,
    func,
    or_,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from app.database import Base

# How long an unconfirmed reservation may hold a seat. Long enough to outlast
# any plausible booking-api commit, short enough that a leaked seat heals
# without operator intervention. See ADR 0001.
RESERVATION_TTL = timedelta(minutes=15)

STATUS_RESERVED = "reserved"
STATUS_CONFIRMED = "confirmed"
STATUS_RELEASED = "released"


class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    time: Mapped[str] = mapped_column(String, nullable=False)
    total_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Vestigial: availability is derived from held reservations (ADR 0001).
    # Retained only because create_all() cannot drop a column.
    available_capacity: Mapped[int] = mapped_column(Integer, nullable=False)


class Reservation(Base):
    __tablename__ = "reservations"

    # Availability is a COUNT over (slot_id, status), so it must be indexed.
    __table_args__ = (Index("ix_reservations_slot_status", "slot_id", "status"),)

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    operation_id: Mapped[str] = mapped_column(String, nullable=False)
    slot_id: Mapped[str] = mapped_column(
        String, ForeignKey("slots.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=STATUS_RESERVED
    )
    # Only meaningful while reserved; confirmed and released seats do not lapse.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def held_filter() -> ColumnElement[bool]:
    """A reservation holds a seat iff confirmed, or reserved and not yet lapsed.

    The single definition of "occupies capacity" -- both the availability check
    and reservation_count use it, so they cannot drift apart. Evaluated against
    the database clock so the two services never disagree about now().
    """
    return or_(
        Reservation.status == STATUS_CONFIRMED,
        and_(
            Reservation.status == STATUS_RESERVED,
            Reservation.expires_at > func.now(),
        ),
    )
