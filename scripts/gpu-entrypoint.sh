#!/bin/bash
# Unified GPU entrypoint: starts Ollama + Piper GPU server
# llama.cpp server started on-demand via API

echo "[gpu] Starting Ollama server..."
ollama serve &
OLLAMA_PID=$!
sleep 2

echo "[gpu] Starting Piper GPU server on :5100..."
python3 /opt/piper-server.py &
PIPER_PID=$!

echo "[gpu] All GPU services started (Ollama PID=$OLLAMA_PID, Piper PID=$PIPER_PID)"

# Wait for any process to exit
wait -n $OLLAMA_PID $PIPER_PID
EXIT_CODE=$?

echo "[gpu] Process exited (code $EXIT_CODE), shutting down..."
kill $OLLAMA_PID $PIPER_PID 2>/dev/null || true
exit $EXIT_CODE
