#!/usr/bin/env bash
# Fix Streamlit 1.58 static HTML template: remove crossorigin from module
# script tag so JS loads without CORS headers in restricted browsers.
# Applied automatically by verify.sh on every gate run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python3"

if [ -x "$VENV_PYTHON" ]; then
    TEMPLATE="$("$VENV_PYTHON" -c 'import streamlit, os; print(os.path.join(os.path.dirname(streamlit.__file__), "static", "index.html"))' 2>/dev/null)"
elif command -v uv >/dev/null 2>&1; then
    TEMPLATE="$(cd "$PROJECT_ROOT" && uv run python -c 'import streamlit, os; print(os.path.join(os.path.dirname(streamlit.__file__), "static", "index.html"))' 2>/dev/null)"
else
    TEMPLATE="$(python3 -c 'import streamlit, os; print(os.path.join(os.path.dirname(streamlit.__file__), "static", "index.html"))' 2>/dev/null || echo "")"
fi

if [ -n "$TEMPLATE" ] && [ -f "$TEMPLATE" ]; then
    if grep -q 'crossorigin' "$TEMPLATE"; then
        sed -i \
          -e 's/<script type="module" crossorigin/<script type="module"/g' \
          -e 's/<link rel="stylesheet" crossorigin/<link rel="stylesheet"/g' \
          "$TEMPLATE"
        sed -i '/^\s*crossorigin$/d' "$TEMPLATE"
        echo "[verify] Fixed Streamlit CORS: removed crossorigin from $TEMPLATE"
    fi
fi
