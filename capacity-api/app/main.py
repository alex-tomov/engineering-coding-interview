from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.database import SessionLocal, create_all
from app.models import Slot
from app.routes.reservations import router


SEED_SLOTS = [
    {
        "id": "slot-munich-0900",
        "location": "Munich",
        "time": "09:00",
        "total_capacity": 30,
        "available_capacity": 30,
    },
    {
        "id": "slot-munich-1000",
        "location": "Munich",
        "time": "10:00",
        "total_capacity": 20,
        "available_capacity": 20,
    },
    {
        "id": "slot-berlin-0900",
        "location": "Berlin",
        "time": "09:00",
        "total_capacity": 1,
        "available_capacity": 1,
    },
]


def seed_slots(db: Session) -> None:
    for slot_data in SEED_SLOTS:
        existing = db.query(Slot).filter(Slot.id == slot_data["id"]).first()
        if not existing:
            db.add(Slot(**slot_data))
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    db = SessionLocal()
    try:
        seed_slots(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Capacity API", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
