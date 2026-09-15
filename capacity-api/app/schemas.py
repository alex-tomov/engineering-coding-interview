from pydantic import BaseModel


class ReservationCreate(BaseModel):
    operation_id: str
    slot_id: str


class ReservationResponse(BaseModel):
    reservation_id: str
    operation_id: str
    slot_id: str
    status: str


class SlotResponse(BaseModel):
    id: str
    location: str
    time: str
    total_capacity: int
    available_capacity: int
    reservation_count: int
