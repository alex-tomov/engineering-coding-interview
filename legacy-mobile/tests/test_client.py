"""Tests for legacy mobile client response parsing.

These tests verify that the deployed mobile client can correctly
parse API v1 responses. The client uses strict Pydantic validation
and will reject unknown status values.
"""

import pytest
from pydantic import ValidationError

from client import BookingResponse


class TestBookingResponseParsing:
    """Verify the legacy client's response parsing behavior."""

    def test_parse_scheduled_booking(self):
        data = {
            "id": "booking-001",
            "slot_id": "slot-munich-0900",
            "status": "scheduled",
            "created_at": "2026-09-15T09:00:00Z",
        }
        booking = BookingResponse.model_validate(data)
        assert booking.id == "booking-001"
        assert booking.status == "scheduled"

    def test_parse_completed_booking(self):
        data = {
            "id": "booking-002",
            "slot_id": "slot-munich-0900",
            "status": "completed",
            "created_at": "2026-09-15T09:00:00Z",
        }
        booking = BookingResponse.model_validate(data)
        assert booking.status == "completed"

    def test_reject_unknown_status(self):
        """The deployed client crashes on unknown status values."""
        data = {
            "id": "booking-003",
            "slot_id": "slot-munich-0900",
            "status": "confirmed",
            "created_at": "2026-09-15T09:00:00Z",
        }
        with pytest.raises(ValidationError):
            BookingResponse.model_validate(data)

    def test_reject_cancelled_status(self):
        """Adding 'cancelled' would break deployed clients."""
        data = {
            "id": "booking-004",
            "slot_id": "slot-munich-0900",
            "status": "cancelled",
            "created_at": "2026-09-15T09:00:00Z",
        }
        with pytest.raises(ValidationError):
            BookingResponse.model_validate(data)

    def test_missing_required_field(self):
        data = {
            "id": "booking-005",
            "slot_id": "slot-munich-0900",
            "status": "scheduled",
        }
        with pytest.raises(ValidationError):
            BookingResponse.model_validate(data)

    def test_extra_fields_ignored(self):
        """The client ignores unknown fields (Pydantic default)."""
        data = {
            "id": "booking-006",
            "slot_id": "slot-munich-0900",
            "status": "scheduled",
            "created_at": "2026-09-15T09:00:00Z",
            "some_new_field": "ignored",
        }
        booking = BookingResponse.model_validate(data)
        assert booking.id == "booking-006"
