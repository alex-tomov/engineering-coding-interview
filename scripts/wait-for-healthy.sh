#!/usr/bin/env bash
set -euo pipefail

echo "Waiting for services to become healthy..."

max_wait=120
elapsed=0

while [ $elapsed -lt $max_wait ]; do
    booking_status=$(docker inspect --format='{{.State.Health.Status}}' engineering-coding-interview-booking-api-1 2>/dev/null || echo "starting")
    capacity_status=$(docker inspect --format='{{.State.Health.Status}}' engineering-coding-interview-capacity-api-1 2>/dev/null || echo "starting")

    if [ "$booking_status" = "healthy" ] && [ "$capacity_status" = "healthy" ]; then
        echo ""
        echo "All services healthy. (${elapsed}s)"
        exit 0
    fi

    printf "\r  booking-api: %-10s  capacity-api: %-10s  (%ds)" "$booking_status" "$capacity_status" "$elapsed"
    sleep 2
    elapsed=$((elapsed + 2))
done

echo ""
echo "Timed out after ${max_wait}s. Check 'make logs' for details."
exit 1
