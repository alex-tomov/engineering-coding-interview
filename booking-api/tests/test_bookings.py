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


def test_retry_with_same_idempotency_key_returns_same_booking(client):
    """INC-1: a retried request must not create a second booking."""
    first = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers=HEADERS,
    )
    second = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers=HEADERS,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]

    listing = client.get("/api/v1/bookings", headers={"X-User-ID": "user-test-1"})
    assert len(listing.json()) == 1


def test_retry_does_not_reserve_capacity_twice(client, mock_capacity_api):
    """INC-1: the downstream reservation must be requested only once."""
    for _ in range(2):
        client.post(
            "/api/v1/bookings",
            json={"slot_id": "slot-munich-0900"},
            headers=HEADERS,
        )

    assert mock_capacity_api.call_count == 1


def test_retry_after_post_commit_failure_returns_original_booking(client):
    """INC-1: the incident path -- first attempt commits, then fails."""
    failed = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers={**HEADERS, "X-Challenge-Fault": "post-commit"},
    )
    assert failed.status_code == 500

    retry = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers=HEADERS,
    )
    assert retry.status_code == 201

    listing = client.get("/api/v1/bookings", headers={"X-User-ID": "user-test-1"})
    assert len(listing.json()) == 1


def test_empty_idempotency_key_is_treated_as_absent(client):
    """An empty header value must not poison every later booking."""
    headers = {"X-User-ID": "user-empty-key", "Idempotency-Key": ""}

    for _ in range(3):
        response = client.post(
            "/api/v1/bookings",
            json={"slot_id": "slot-munich-0900"},
            headers=headers,
        )
        assert response.status_code == 201


def test_same_key_with_different_slot_is_rejected(client):
    """Same key, different intent -- replaying would confirm the wrong slot."""
    first = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers=HEADERS,
    )
    assert first.status_code == 201

    second = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-berlin-0900"},
        headers=HEADERS,
    )
    assert second.status_code == 422


def test_separator_in_user_or_key_does_not_collide(client):
    """("alice:b", "c") and ("alice", "b:c") must not map to one booking."""
    first = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers={"X-User-ID": "alice", "Idempotency-Key": "b:c"},
    )
    second = client.post(
        "/api/v1/bookings",
        json={"slot_id": "slot-munich-0900"},
        headers={"X-User-ID": "alice:b", "Idempotency-Key": "c"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
