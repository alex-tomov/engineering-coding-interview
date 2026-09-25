from datetime import datetime, timedelta, timezone

from app.models import STATUS_CONFIRMED, STATUS_RESERVED, Reservation, Slot


def _slot(db_session, slot_id, capacity=1):
    db_session.add(
        Slot(
            id=slot_id,
            location="Test",
            time="09:00",
            total_capacity=capacity,
            available_capacity=capacity,
        )
    )
    db_session.commit()


def test_released_reservation_frees_capacity(client, db_session):
    """BUG-1: a released seat must become bookable again."""
    _slot(db_session, "slot-release")

    client.post(
        "/internal/v1/reservations",
        json={"operation_id": "op-to-release", "slot_id": "slot-release"},
    )
    assert client.get("/internal/v1/slots/slot-release").json()["available_capacity"] == 0

    released = client.post("/internal/v1/reservations/op-to-release/release")
    assert released.status_code == 200

    slot = client.get("/internal/v1/slots/slot-release").json()
    assert slot["available_capacity"] == 1
    assert slot["reservation_count"] == 0

    # The freed seat is genuinely usable by someone else.
    again = client.post(
        "/internal/v1/reservations",
        json={"operation_id": "op-someone-else", "slot_id": "slot-release"},
    )
    assert again.status_code == 201


def test_lapsed_reservation_frees_capacity_without_a_sweeper(client, db_session):
    """AC-1: an unconfirmed seat returns on expiry, with no job having run."""
    _slot(db_session, "slot-lapse")
    db_session.add(
        Reservation(
            id="res-lapsed",
            operation_id="op-lapsed",
            slot_id="slot-lapse",
            status=STATUS_RESERVED,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    db_session.commit()

    slot = client.get("/internal/v1/slots/slot-lapse").json()
    assert slot["available_capacity"] == 1
    assert slot["reservation_count"] == 0

    taken = client.post(
        "/internal/v1/reservations",
        json={"operation_id": "op-new-owner", "slot_id": "slot-lapse"},
    )
    assert taken.status_code == 201


def test_confirmed_reservation_never_lapses(client, db_session):
    """AC-4: a committed booking keeps its seat indefinitely."""
    _slot(db_session, "slot-confirmed")
    db_session.add(
        Reservation(
            id="res-old",
            operation_id="op-old",
            slot_id="slot-confirmed",
            status=STATUS_CONFIRMED,
            expires_at=datetime.now(timezone.utc) - timedelta(days=365),
        )
    )
    db_session.commit()

    slot = client.get("/internal/v1/slots/slot-confirmed").json()
    assert slot["available_capacity"] == 0
    assert slot["reservation_count"] == 1


def test_release_is_idempotent(client, db_session):
    """AC-2: releasing twice must not credit the seat twice."""
    _slot(db_session, "slot-twice", capacity=2)
    client.post(
        "/internal/v1/reservations",
        json={"operation_id": "op-twice", "slot_id": "slot-twice"},
    )

    for _ in range(3):
        assert (
            client.post("/internal/v1/reservations/op-twice/release").status_code
            == 200
        )

    slot = client.get("/internal/v1/slots/slot-twice").json()
    assert slot["available_capacity"] == 2


def test_release_unknown_operation_succeeds(client):
    """AC-6: a compensating call must not fail because its subject is gone."""
    assert (
        client.post("/internal/v1/reservations/never-existed/release").status_code
        == 200
    )


def test_release_does_not_strip_a_confirmed_seat(client, db_session):
    """AC-4: release must not reclaim a seat behind a real booking."""
    _slot(db_session, "slot-keep")
    client.post(
        "/internal/v1/reservations",
        json={"operation_id": "op-keep", "slot_id": "slot-keep"},
    )
    client.post("/internal/v1/reservations/op-keep/confirm")
    client.post("/internal/v1/reservations/op-keep/release")

    assert client.get("/internal/v1/slots/slot-keep").json()["available_capacity"] == 0


def test_confirm_unknown_operation_is_404(client):
    assert client.post("/internal/v1/reservations/nope/confirm").status_code == 404


def test_confirm_after_release_is_409(client, db_session):
    """A seat already given away cannot be silently retaken."""
    _slot(db_session, "slot-gone")
    client.post(
        "/internal/v1/reservations",
        json={"operation_id": "op-gone", "slot_id": "slot-gone"},
    )
    client.post("/internal/v1/reservations/op-gone/release")

    assert client.post("/internal/v1/reservations/op-gone/confirm").status_code == 409


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_reservation(client, db_session):
    db_session.add(
        Slot(
            id="slot-test-01",
            location="Test",
            time="10:00",
            total_capacity=5,
            available_capacity=5,
        )
    )
    db_session.commit()

    response = client.post(
        "/internal/v1/reservations",
        json={"operation_id": "op-001", "slot_id": "slot-test-01"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["operation_id"] == "op-001"
    assert data["slot_id"] == "slot-test-01"
    assert data["status"] == "reserved"
    assert "reservation_id" in data


def test_slot_not_found(client):
    response = client.post(
        "/internal/v1/reservations",
        json={"operation_id": "op-002", "slot_id": "nonexistent"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Slot not found"


def test_capacity_exhausted(client, db_session):
    db_session.add(
        Slot(
            id="slot-limited",
            location="Berlin",
            time="09:00",
            total_capacity=1,
            available_capacity=1,
        )
    )
    db_session.commit()

    first = client.post(
        "/internal/v1/reservations",
        json={"operation_id": "op-003", "slot_id": "slot-limited"},
    )
    assert first.status_code == 201

    second = client.post(
        "/internal/v1/reservations",
        json={"operation_id": "op-004", "slot_id": "slot-limited"},
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "No capacity available"


def test_get_slot(client, db_session):
    db_session.add(
        Slot(
            id="slot-info",
            location="Munich",
            time="09:00",
            total_capacity=3,
            available_capacity=3,
        )
    )
    db_session.commit()

    response = client.get("/internal/v1/slots/slot-info")
    assert response.status_code == 200

    data = response.json()
    assert data["id"] == "slot-info"
    assert data["total_capacity"] == 3
    assert data["available_capacity"] == 3
    assert data["reservation_count"] == 0


def test_get_slot_not_found(client):
    response = client.get("/internal/v1/slots/nonexistent")
    assert response.status_code == 404
