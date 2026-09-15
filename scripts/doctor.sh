#!/usr/bin/env bash
set -euo pipefail

echo "=== Interview Challenge: Environment Check ==="
echo ""

errors=0

# Check Docker
if command -v docker &> /dev/null; then
    echo "[ok] Docker found: $(docker --version)"
else
    echo "[FAIL] Docker not found. Install Docker Desktop: https://www.docker.com/products/docker-desktop"
    errors=$((errors + 1))
fi

# Check Docker Compose
if docker compose version &> /dev/null; then
    echo "[ok] Docker Compose found: $(docker compose version --short)"
else
    echo "[FAIL] Docker Compose not found."
    errors=$((errors + 1))
fi

# Check Docker daemon
if docker info &> /dev/null 2>&1; then
    echo "[ok] Docker daemon running"
else
    echo "[FAIL] Docker daemon not running. Start Docker Desktop."
    errors=$((errors + 1))
fi

# Check ports
for port in 18080 18081 15432; do
    if lsof -i :$port &> /dev/null 2>&1; then
        echo "[FAIL] Port $port is in use"
        errors=$((errors + 1))
    else
        echo "[ok] Port $port is available"
    fi
done

# Check curl
if command -v curl &> /dev/null; then
    echo "[ok] curl found"
else
    echo "[FAIL] curl not found"
    errors=$((errors + 1))
fi

echo ""
if [ $errors -eq 0 ]; then
    echo "All checks passed. Run 'make up' to start."
else
    echo "$errors check(s) failed. Fix the issues above before starting."
    exit 1
fi
