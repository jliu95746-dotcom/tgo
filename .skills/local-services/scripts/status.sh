#!/usr/bin/env bash
# local-services/status: check what's running
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

is_port_open() {
  if command -v lsof >/dev/null 2>&1 &&
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; then
    return 0
  fi
  (: > "/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
}

echo "=== Service Status ==="
echo ""

# Infrastructure
echo "Infrastructure:"
if is_port_open "${POSTGRES_HOST_PORT:-15432}"; then
  printf "  ✓ %-20s :%s\n" "PostgreSQL" "${POSTGRES_HOST_PORT:-15432}"
elif is_port_open 5432; then
  printf "  ✓ %-20s :%s\n" "PostgreSQL" 5432
else
  printf "  ✗ %-20s :%s\n" "PostgreSQL" "${POSTGRES_HOST_PORT:-15432}"
fi
for PAIR in "6379:Redis" "5001:WuKongIM-API" "5100:WuKongIM-TCP" "5200:WuKongIM-WS"; do
  PORT="${PAIR%%:*}"
  NAME="${PAIR#*:}"
  if is_port_open "$PORT"; then
    printf "  ✓ %-20s :%s\n" "$NAME" "$PORT"
  else
    printf "  ✗ %-20s :%s\n" "$NAME" "$PORT"
  fi
done

echo ""
echo "Minimum services:"
for PAIR in "8000:tgo-api" "8081:tgo-ai"; do
  PORT="${PAIR%%:*}"
  NAME="${PAIR#*:}"
  if is_port_open "$PORT"; then
    printf "  ✓ %-20s :%s\n" "$NAME" "$PORT"
  else
    printf "  ✗ %-20s :%s\n" "$NAME" "$PORT"
  fi
done

echo ""
echo "Optional services:"
for PAIR in "18082:tgo-rag" "8003:tgo-platform" "8004:tgo-workflow" "8090:tgo-plugin-runtime" "8085:tgo-device-control" "5173:tgo-web" "5174:tgo-widget-js"; do
  PORT="${PAIR%%:*}"
  NAME="${PAIR#*:}"
  if is_port_open "$PORT"; then
    printf "  ✓ %-20s :%s\n" "$NAME" "$PORT"
  else
    printf "  · %-20s :%s (not running)\n" "$NAME" "$PORT"
  fi
done

# Quick health check on API if running
echo ""
if is_port_open 8000; then
  echo "API health:"
  if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    echo "  ✓ GET /health OK"
  elif curl -sf http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    echo "  ✓ GET /api/v1/health OK"
  else
    echo "  ⚠ Port open but health check failed"
  fi
fi
