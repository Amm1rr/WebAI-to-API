#!/bin/bash

cd "$(dirname "$0")"

PID_FILE="/tmp/webai-to-api.pid"
LOG_FILE="/tmp/webai-to-api.log"

# --- Stop Command ---
if [[ "$1" == "--stop" ]]; then
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "--> Stopping WebAI-to-API (PID $PID)..."
            kill "$PID"
            # Wait up to 10 seconds for graceful shutdown
            for i in $(seq 1 10); do
                sleep 1
                kill -0 "$PID" 2>/dev/null || break
            done
            if kill -0 "$PID" 2>/dev/null; then
                echo "--> Process did not stop gracefully, forcing termination..."
                kill -9 "$PID"
            fi
            rm -f "$PID_FILE"
            echo "--> WebAI-to-API stopped."
        else
            echo "--> No running process found for PID $PID. Cleaning up stale PID file."
            rm -f "$PID_FILE"
        fi
    else
        echo "--> WebAI-to-API is not running (no PID file found)."
    fi
    exit 0
fi

echo "------------------------------------------"
echo " Refreshing WebAI-to-API... "
echo "------------------------------------------"

# --- Check if already running ---
if [[ -f "$PID_FILE" ]]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "--> WebAI-to-API is already running (PID $PID)."
        echo "--> Run '$0 --stop' to stop it first, or check logs at $LOG_FILE"
        exit 1
    else
        echo "--> Stale PID file found. Cleaning up..."
        rm -f "$PID_FILE"
    fi
fi

# 1. Pull the latest code updates
echo "--> Checking for repository updates..."
git pull

# 2. Sync the virtual environment
# 'poetry install' checks pyproject.toml and only installs new/updated packages
echo "--> Syncing dependencies..."
poetry install --sync

# 3. Launch the server as a background daemon
echo "--> Launching Gemini API bridge in the background..."
echo "--> Logs will be written to $LOG_FILE"
nohup poetry run python src/run.py > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "------------------------------------------"
echo "--> WebAI-to-API started (PID $(cat $PID_FILE))."
echo "--> Run '$0 --stop' to stop it."
