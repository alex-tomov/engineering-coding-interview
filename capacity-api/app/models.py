import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    time: Mapped[str] = mapped_column(String, nullable=False)
    total_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    available_capacity: Mapped[int] = mapped_column(Integer, nullable=False)


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # The caller's idempotency token: one reservation per operation, so a
    # retried call cannot consume capacity twice.
    operation_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    slot_id: Mapped[str] = mapped_column(
        String, ForeignKey("slots.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="reserved")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
