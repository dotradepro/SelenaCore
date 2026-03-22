#!/bin/bash
# Start Core API (:7070) and UI Core (:80) in parallel

set -e

echo "[start.sh] Starting Core API on :7070..."
python -m uvicorn core.main:app --host 0.0.0.0 --port 7070 --no-access-log &
CORE_PID=$!

echo "[start.sh] Starting UI Core on :80..."
python -m uvicorn system_modules.ui_core.server:ui_app --host 0.0.0.0 --port 80 --no-access-log &
UI_PID=$!

echo "[start.sh] Core API PID=$CORE_PID  UI Core PID=$UI_PID"

# If either process exits, kill the other and exit
wait -n $CORE_PID $UI_PID
EXIT_CODE=$?

echo "[start.sh] One of the processes exited (code $EXIT_CODE), shutting down..."
kill $CORE_PID $UI_PID 2>/dev/null || true
exit $EXIT_CODE
