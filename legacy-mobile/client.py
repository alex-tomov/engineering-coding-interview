"""
Legacy Mobile Client v1.0

This module models the deployed mobile application's expectations of the
booking API v1. It is used to validate that API changes remain backwards
compatible with existing installations.

The mobile app:
- Calls POST /api/v1/bookings with Idempotency-Key header
- Parses responses through strict models
- Accepts ONLY the status values that existed at release time
- Retries failed requests with the same Idempotency-Key
"""

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict


class BookingResponse(BaseModel):
    """Strict response model matching the deployed mobile client.

    The mobile app validates status against a fixed enum.
    Unknown status values cause the booking screen to crash.
    """

    model_config = ConfigDict(strict=True)

    id: str
    slot_id: str
    status: Literal["scheduled", "completed"]
    created_at: str


class LegacyMobileClient:
    """Simulates the deployed mobile application."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.client = httpx.Client(base_url=base_url, timeout=30.0)

    def create_booking(
        self, user_id: str, slot_id: str, idempotency_key: str
    ) -> BookingResponse:
        response = self.client.post(
            "/api/v1/bookings",
            json={"slot_id": slot_id},
            headers={
                "X-User-ID": user_id,
                "Idempotency-Key": idempotency_key,
            },
        )
        response.raise_for_status()
        return BookingResponse.model_validate(response.json())

    def get_booking(self, user_id: str, booking_id: str) -> BookingResponse:
        response = self.client.get(
            f"/api/v1/bookings/{booking_id}",
            headers={"X-User-ID": user_id},
        )
        response.raise_for_status()
        return BookingResponse.model_validate(response.json())

    def list_bookings(self, user_id: str) -> list[BookingResponse]:
        response = self.client.get(
            "/api/v1/bookings",
            headers={"X-User-ID": user_id},
        )
        response.raise_for_status()
        return [BookingResponse.model_validate(b) for b in response.json()]
