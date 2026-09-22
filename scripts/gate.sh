#!/usr/bin/env bash
# Test gate: run full pytest suite, exit non-zero on any failure.
# Usage: bash scripts/gate.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# Use a clean basetemp to avoid Windows file-lock issues on default pytest temp dir
BASETEMP="${LOCALAPPDATA:-/tmp}/pytest-gate-$$"
rm -rf "$BASETEMP" 2>/dev/null || true
mkdir -p "$BASETEMP"

echo "=== Running full test suite ==="
# 优先用项目 venv（生产服务器），否则回退系统 python
PYBIN="${PYTHON:-}"
if [ -z "$PYBIN" ]; then
    if [ -x ".venv/bin/python" ]; then PYBIN=".venv/bin/python";
    elif command -v python3 >/dev/null 2>&1; then PYBIN="python3";
    else PYBIN="python"; fi
fi
"$PYBIN" -m pytest --tb=short -q --basetemp="$BASETEMP"
EXIT_CODE=$?

rm -rf "$BASETEMP" 2>/dev/null || true

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "=== Gate passed ==="
else
    echo ""
    echo "=== Gate FAILED (exit $EXIT_CODE) ==="
fi

exit $EXIT_CODE
