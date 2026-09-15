from app.models import Slot


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
