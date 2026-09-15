HEADERS = {"X-User-ID": "user-test-1", "Idempotency-Key": "key-1"}


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


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
