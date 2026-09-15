from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import create_tables
from app.routes.bookings import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(title="Booking API", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
