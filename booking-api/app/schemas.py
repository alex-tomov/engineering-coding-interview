from pydantic import BaseModel


class CreateBookingRequest(BaseModel):
    slot_id: str


class BookingResponse(BaseModel):
    id: str
    slot_id: str
    status: str
    created_at: str
