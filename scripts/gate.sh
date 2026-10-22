#!/usr/bin/env bash
# Test gate: run full pytest suite, exit non-zero on any failure.
# 基线只升不降：scripts/gate-baseline.txt 记录通过数下限，全绿但通过数低于下限同样判失败；
# 有意调整下限（新增/删除测试）时更新该文件并随同一批次提交。
# Usage: bash scripts/gate.sh
set -uo pipefail

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
if OUT=$("$PYBIN" -m pytest --tb=short -q --basetemp="$BASETEMP" 2>&1); then
    EXIT_CODE=0
else
    EXIT_CODE=$?
fi
printf '%s\n' "$OUT"

rm -rf "$BASETEMP" 2>/dev/null || true

# 解析通过数，比对基线下限（只升不降）
PASSED=$(printf '%s\n' "$OUT" | grep -oE '[0-9]+ passed' | head -1 | awk '{print $1}')
FLOOR=0
if [ -f scripts/gate-baseline.txt ]; then
    FLOOR=$(tr -dc '0-9' < scripts/gate-baseline.txt)
    FLOOR=${FLOOR:-0}
fi

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "=== Gate FAILED (exit $EXIT_CODE) ==="
    exit $EXIT_CODE
fi

if [ -n "$PASSED" ] && [ "$PASSED" -lt "$FLOOR" ]; then
    echo ""
    echo "=== Gate FAILED: ${PASSED} passed 低于基线下限 ${FLOOR}（基线只升不降；有意调整请更新 scripts/gate-baseline.txt 并随同一批次提交）==="
    exit 1
fi

echo ""
echo "=== Gate passed: ${PASSED:-?} passed (基线下限 ${FLOOR}) ==="
