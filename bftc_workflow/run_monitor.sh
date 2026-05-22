#!/usr/bin/env bash
# BFTC Monitor & Regulator Startup Helper Script
# Autodetects virtual environments and launches the Dash Web/GUI app robustly.

# Get the absolute directory path of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

echo "=== BFTC Monitor Startup Helper ==="
echo "Working Directory: $SCRIPT_DIR"

# Find and activate python virtual environment if it exists
if [ -d "../.venv" ]; then
    echo "Detected virtual environment at ../.venv"
    PYTHON_EXE="../.venv/bin/python"
elif [ -d ".venv" ]; then
    echo "Detected virtual environment at .venv"
    PYTHON_EXE=".venv/bin/python"
else
    echo "No virtual environment found. Falling back to system python3..."
    PYTHON_EXE="python3"
fi

# Run the monitor
echo "Launching bftc_monitor.py..."
"$PYTHON_EXE" bftc_monitor.py "$@"
