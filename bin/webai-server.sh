#!/usr/bin/env bash
# bin/webai-server

# Load optional .env if present
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# --- Nix-specific addition ---
# Check if we are in a nix develop shell with a venv activated
if [ -z "$VIRTUAL_ENV" ]; then
    # If not in an activated venv, assume we need to set one up within the script
    VENV_DIR="$TMPDIR/webai_venv_script"
    if [ ! -d "$VENV_DIR" ]; then
        echo "Creating venv in $VENV_DIR (from script)"
        python -m venv "$VENV_DIR"
        source "$VENV_DIR/bin/activate"
        pip install --upgrade pip
        pip install -e . # Install project dependencies
    else
        # Activate existing venv
        source "$VENV_DIR/bin/activate"
    fi
fi
# --------------------------

# Set PYTHONPATH for the src layout
export PYTHONPATH="$(dirname "$0")/../src:$PYTHONPATH"

# Run the *interactive* run.py script, not uvicorn directly
exec python src/run.py # Execute the script that provides the menu