#!/usr/bin/env bash
set -euo pipefail

BOOKING_API="http://localhost:18080"
CAPACITY_API="http://localhost:18081"
USER_ID="user-reproduce-001"
IDEMPOTENCY_KEY="idempotency-reproduce-$(date +%s)"
SLOT_ID="slot-munich-0900"

echo "================================================================"
echo "  Duplicate Booking Incident -- Reproduction"
echo "================================================================"
echo ""
echo "Scenario: A user books an appointment, gets a timeout,"
echo "taps Retry, and ends up with two bookings."
echo ""
echo "Using:"
echo "  User:            $USER_ID"
echo "  Idempotency-Key: $IDEMPOTENCY_KEY"
echo "  Slot:            $SLOT_ID"
echo ""

# Step 0: Check slot capacity before
echo "-- Step 0: Initial slot capacity --"
BEFORE=$(curl -s "$CAPACITY_API/internal/v1/slots/$SLOT_ID")
echo "$BEFORE" | python3 -m json.tool 2>/dev/null || echo "$BEFORE"
echo ""

# Step 1: First request -- will fail AFTER committing (simulated timeout)
echo "-- Step 1: Send booking request (fault injection: post-commit failure) --"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BOOKING_API/api/v1/bookings" \
    -H "Content-Type: application/json" \
    -H "X-User-ID: $USER_ID" \
    -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
    -H "X-Challenge-Fault: post-commit" \
    -d "{\"slot_id\": \"$SLOT_ID\"}")
echo "Response status: $HTTP_CODE"
echo "(The booking and reservation were committed, but the client saw an error.)"
echo ""

# Step 2: Retry -- same idempotency key, same payload
echo "-- Step 2: Retry with the same Idempotency-Key (simulating user tap) --"
RETRY_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BOOKING_API/api/v1/bookings" \
    -H "Content-Type: application/json" \
    -H "X-User-ID: $USER_ID" \
    -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
    -d "{\"slot_id\": \"$SLOT_ID\"}")
RETRY_CODE=$(echo "$RETRY_RESPONSE" | tail -1)
RETRY_BODY=$(echo "$RETRY_RESPONSE" | sed '$d')
echo "Response status: $RETRY_CODE"
echo "$RETRY_BODY" | python3 -m json.tool 2>/dev/null || echo "$RETRY_BODY"
echo ""

# Step 3: Check how many bookings exist for this user
echo "-- Step 3: List bookings for this user --"
BOOKINGS=$(curl -s "$BOOKING_API/api/v1/bookings" -H "X-User-ID: $USER_ID")
echo "$BOOKINGS" | python3 -m json.tool 2>/dev/null || echo "$BOOKINGS"
BOOKING_COUNT=$(echo "$BOOKINGS" | python3 -c "import sys, json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
echo ""

# Step 4: Check slot capacity after
echo "-- Step 4: Slot capacity after --"
AFTER=$(curl -s "$CAPACITY_API/internal/v1/slots/$SLOT_ID")
echo "$AFTER" | python3 -m json.tool 2>/dev/null || echo "$AFTER"
echo ""

# Verdict
echo "================================================================"
echo "  RESULT"
echo "================================================================"
if [ "$BOOKING_COUNT" = "2" ]; then
    echo ""
    echo "  !! INCIDENT REPRODUCED"
    echo ""
    echo "  Bookings created: $BOOKING_COUNT (expected: 1)"
    echo "  The same idempotency key produced two separate bookings."
    echo "  Capacity was consumed twice."
else
    echo ""
    echo "  Bookings created: $BOOKING_COUNT"
    echo "  (Expected 2 for reproduction -- check service logs)"
fi
echo ""
echo "================================================================"
