#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd -- "$SCRIPT_DIR"

PYTHON=""
for candidate in python3 python; do
	if command -v "$candidate" >/dev/null 2>&1 &&
		"$candidate" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)' >/dev/null 2>&1; then
		PYTHON="$candidate"
		break
	fi
done

if [[ -z "$PYTHON" ]]; then
	printf '%s\n' \
		'ERROR: Python 3.11 or 3.12 is required, but no supported interpreter was found.' \
		'Install Python 3.11 or 3.12, then rerun this script.' >&2
	exit 1
fi

if ! command -v poetry >/dev/null 2>&1; then
	printf '%s\n' \
		'ERROR: Poetry was not found on PATH.' \
		'Install Poetry from https://python-poetry.org/docs/#installation, then rerun this script.' >&2
	exit 1
fi

if ! poetry --version >/dev/null 2>&1; then
	printf '%s\n' \
		'ERROR: Poetry is on PATH but could not execute.' \
		'Check the Poetry installation and rerun this script.' >&2
	exit 1
fi

run_phase() {
	local name="$1"
	shift
	printf '==> %s\n' "$name"
	"$@" || {
		local status=$?
		printf 'ERROR: %s failed (exit code %s).\n' "$name" "$status" >&2
		return "$status"
	}
}

run_phase 'bootstrap' "$PYTHON" scripts/bootstrap.py
run_phase 'doctor' "$PYTHON" scripts/doctor.py

cat <<'EOF'

Setup complete.
Next steps:
  Authentication (when needed): poetry run python verify_login.py
  Start server:                 poetry run python src/run.py
  Dashboard:                    http://localhost:6969/ui
EOF
