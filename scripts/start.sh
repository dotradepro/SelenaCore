#!/bin/bash
# Start Core API (:7070) and UI Core (:80 HTTP + :443 HTTPS) in parallel

set -e

# Auto-generate self-signed TLS certificate if not present
TLS_CERT="/secure/tls/selena.crt"
TLS_KEY="/secure/tls/selena.key"
if [ ! -f "$TLS_CERT" ] || [ ! -f "$TLS_KEY" ]; then
  echo "[start.sh] Generating self-signed TLS certificate..."
  python3 generate_https_cert.py && echo "[start.sh] TLS cert generated OK" \
    || echo "[start.sh] WARNING: cert generation failed, HTTPS will be disabled"
fi

echo "[start.sh] Starting Core API on :7070..."
python -m uvicorn core.main:app --host 0.0.0.0 --port 7070 --no-access-log &
CORE_PID=$!

echo "[start.sh] Starting UI Core on :80 (HTTP)..."
python -m uvicorn system_modules.ui_core.server:ui_app --host 0.0.0.0 --port 80 --no-access-log &
UI_PID=$!

# Start HTTPS if TLS certificate exists
TLS_CERT="/secure/tls/selena.crt"
TLS_KEY="/secure/tls/selena.key"
HTTPS_PID=""
if [ -f "$TLS_CERT" ] && [ -f "$TLS_KEY" ]; then
  echo "[start.sh] Starting UI Core on :443 (HTTPS)..."
  python -m uvicorn system_modules.ui_core.server:ui_app --host 0.0.0.0 --port 443 \
    --ssl-certfile "$TLS_CERT" --ssl-keyfile "$TLS_KEY" --no-access-log &
  HTTPS_PID=$!
  echo "[start.sh] HTTPS PID=$HTTPS_PID"
else
  echo "[start.sh] No TLS certificate found, HTTPS disabled"
fi

echo "[start.sh] Core API PID=$CORE_PID  UI Core PID=$UI_PID"

# If any process exits, kill all others and exit
wait -n $CORE_PID $UI_PID ${HTTPS_PID:+$HTTPS_PID}
EXIT_CODE=$?

echo "[start.sh] One of the processes exited (code $EXIT_CODE), shutting down..."
kill $CORE_PID $UI_PID $HTTPS_PID 2>/dev/null || true
exit $EXIT_CODE
