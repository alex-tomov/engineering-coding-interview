from unittest.mock import MagicMock, patch

import httpx

from app.models import Booking, OutboxEntry

HEADERS = {"X-User-ID": "user-test-1", "Idempotency-Key": "key-1"}


def _capacity_response(status_code: int) -> MagicMock:
    """A stand-in for the capacity-api response, including its error path."""
    response = MagicMock()
    response.status_code = status_code
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=MagicMock(), response=response
        )
    return response


def test_full_slot_returns_409(client, mock_capacity_api):
    """BUG-1/AC-7: 'no capacity' is the client's problem, not a server error."""
    mock_capacity_api.return_value = _capacity_response(409)

    response = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-berlin-0900"},
        headers=HEADERS,
    )

    assert response.status_code == 409


def test_create_booking(client):
    response = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers=HEADERS,
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["slot_id"] == "slot-munich-0900"
    assert data["status"] == "scheduled"
    assert "created_at" in data


def test_create_booking_missing_user(client):
    response = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
    )
    assert response.status_code == 400


def test_get_booking(client):
    create_resp = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers=HEADERS,
    )
    booking_id = create_resp.json()["id"]

    get_resp = client.get(
        f"/api/v1/bookings/{booking_id}",
        headers={"X-User-ID": "user-test-1"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == booking_id


def test_get_booking_not_found(client):
    response = client.get(
        "/api/v1/bookings/nonexistent-id",
        headers={"X-User-ID": "user-test-1"},
    )
    assert response.status_code == 404


def test_list_bookings(client):
    for i in range(2):
        client.post(
            "/api/v1/bookings",
            json={"slot_id": "slot-munich-0900"},
            headers={**HEADERS, "Idempotency-Key": f"key-{i}"},
        )

    response = client.get(
        "/api/v1/bookings",
        headers={"X-User-ID": "user-test-1"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_list_bookings_user_isolation(client):
    client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers={"X-User-ID": "user-a", "Idempotency-Key": "key-a"},
    )
    client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers={"X-User-ID": "user-b", "Idempotency-Key": "key-b"},
    )

    resp_a = client.get("/api/v1/bookings", headers={"X-User-ID": "user-a"})
    resp_b = client.get("/api/v1/bookings", headers={"X-User-ID": "user-b"})
    assert len(resp_a.json()) == 1
    assert len(resp_b.json()) == 1


def test_failed_commit_releases_the_seat(client, mock_capacity_api):
    """BUG-1: a reservation whose booking never commits must not hold a seat."""
    with patch(
        "sqlalchemy.orm.Session.commit", side_effect=RuntimeError("database down")
    ):
        response = client.post(
            "/api/v1/bookings",
            json={"slot_id": "slot-munich-0900"},
            headers=HEADERS,
        )

    assert response.status_code == 500

    released = [
        call
        for call in mock_capacity_api.call_args_list
        if call.args and call.args[0].endswith("/release")
    ]
    assert len(released) == 1


def test_successful_booking_confirms_the_seat(client, mock_capacity_api):
    """The seat must stop being eligible for expiry once the booking is durable."""
    client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers=HEADERS,
    )

    confirmed = [
        call
        for call in mock_capacity_api.call_args_list
        if call.args and call.args[0].endswith("/confirm")
    ]
    released = [
        call
        for call in mock_capacity_api.call_args_list
        if call.args and call.args[0].endswith("/release")
    ]
    assert len(confirmed) == 1
    assert released == []


def test_booking_and_confirm_intent_commit_together(client, db_session):
    """The outbox row is written in the booking's own transaction."""
    client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers=HEADERS,
    )

    # Confirm succeeded, so the intent was settled and removed.
    assert db_session.query(OutboxEntry).count() == 0
    assert db_session.query(Booking).count() == 1


def test_failed_confirm_leaves_the_intent_durable(client, mock_capacity_api, db_session):
    """A lost confirm must not be lost work -- the intent outlives the request."""

    def _reserve_ok_confirm_fails(url, *args, **kwargs):
        if url.endswith("/confirm"):
            raise httpx.ConnectError("capacity-api unreachable")
        return _capacity_response(201)

    mock_capacity_api.side_effect = _reserve_ok_confirm_fails

    response = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers=HEADERS,
    )

    # The user still gets their booking -- the confirm is our problem, not theirs.
    assert response.status_code == 201
    assert db_session.query(Booking).count() == 1

    entry = db_session.query(OutboxEntry).one()
    assert entry.operation_id == response.json()["id"]
    assert entry.attempts == 1


def test_later_request_drains_a_stranded_intent(client, mock_capacity_api, db_session):
    """Recovery without a scheduler: the next booking finishes the last one."""

    def _confirm_fails(url, *args, **kwargs):
        if url.endswith("/confirm"):
            raise httpx.ConnectError("capacity-api unreachable")
        return _capacity_response(201)

    mock_capacity_api.side_effect = _confirm_fails
    stranded = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers=HEADERS,
    ).json()["id"]
    assert db_session.query(OutboxEntry).count() == 1

    # capacity-api comes back; an unrelated booking arrives.
    mock_capacity_api.side_effect = None
    mock_capacity_api.return_value = _capacity_response(201)
    client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers={"X-User-ID": "user-test-2", "Idempotency-Key": "key-2"},
    )

    assert db_session.query(OutboxEntry).count() == 0
    confirmed = [
        call
        for call in mock_capacity_api.call_args_list
        if call.args and call.args[0].endswith(f"/{stranded}/confirm")
    ]
    assert confirmed, "the stranded intent should have been retried"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
