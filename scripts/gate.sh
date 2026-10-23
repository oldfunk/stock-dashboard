#!/usr/bin/env bash
# 分层测试门禁（2026-10-24）：
#  - 母项目用例（排除 paper_trading/）：**权威门禁**，必须全绿，基线强制（scripts/gate-baseline.txt）
#  - vendor 用例（paper_trading/tests）：单独跑并记录；**域内失败不阻塞母项目**（协议 §6），
#    已知问题列入 scripts/gate-exclusions.txt（带子项目 Issue 链接）
# 设计动机：vendor 用例与母项目用例混在一个池子里时，一个域内测试 bug 会阻断母项目所有无关改动。
# Usage: bash scripts/gate.sh
set -uo pipefail

cd "$(dirname "$0")/.."

# Use a clean basetemp to avoid Windows file-lock issues on default pytest temp dir
BASETEMP="${LOCALAPPDATA:-/tmp}/pytest-gate-$$"
rm -rf "$BASETEMP" 2>/dev/null || true
mkdir -p "$BASETEMP"

# 优先用项目 venv（生产服务器），否则回退系统 python
PYBIN="${PYTHON:-}"
if [ -z "$PYBIN" ]; then
    if [ -x ".venv/bin/python" ]; then PYBIN=".venv/bin/python";
    elif command -v python3 >/dev/null 2>&1; then PYBIN="python3";
    else PYBIN="python"; fi
fi

# vendor 已知问题排除清单（带 Issue 链接；修复后移出并恢复基线）
DESELECT=()
if [ -f scripts/gate-exclusions.txt ]; then
    while IFS= read -r _line; do
        _line="${_line%%#*}"
        _line="$(printf '%s' "$_line" | tr -d ' \r')"
        [ -n "$_line" ] && DESELECT+=(--deselect "$_line")
    done < scripts/gate-exclusions.txt
fi

_floor() {  # $1=基线文件
    local f=0
    if [ -f "$1" ]; then f=$(tr -dc '0-9' < "$1"); f=${f:-0}; fi
    printf '%s' "$f"
}

_run() {  # $1=标签；其余=pytest 参数
    local label="$1"; shift
    local out rc
    if out=$("$PYBIN" -m pytest --tb=short -q --basetemp="$BASETEMP" "$@" 2>&1); then rc=0; else rc=$?; fi
    printf '%s\n' "$out" | tail -3
    printf '%s\n' "$out" | grep -oE '[0-9]+ passed' | head -1 | awk '{print $1}' > "$BASETEMP/$label.count"
    printf '%s' "$rc" > "$BASETEMP/$label.rc"
}

echo "=== 母项目门禁（权威：排除 paper_trading/）==="
_run mother --ignore=paper_trading
MOTHER_PASSED=$(cat "$BASETEMP/mother.count" 2>/dev/null)
MOTHER_RC=$(cat "$BASETEMP/mother.rc" 2>/dev/null)
MOTHER_FLOOR=$(_floor scripts/gate-baseline.txt)

echo "=== vendor 套件（记录，不阻塞母项目）==="
_run vendor paper_trading/tests ${DESELECT[@]+"${DESELECT[@]}"}
VENDOR_PASSED=$(cat "$BASETEMP/vendor.count" 2>/dev/null)
VENDOR_RC=$(cat "$BASETEMP/vendor.rc" 2>/dev/null)
VENDOR_FLOOR=$(_floor scripts/gate-baseline-vendor.txt)

# ---- 判定：母项目必须全绿且不低于下限 ----
if [ "$MOTHER_RC" != "0" ]; then
    echo ""
    echo "=== Gate FAILED：母项目用例失败（exit $MOTHER_RC）==="
    exit "$MOTHER_RC"
fi
if [ -n "$MOTHER_PASSED" ] && [ "$MOTHER_PASSED" -lt "$MOTHER_FLOOR" ]; then
    echo ""
    echo "=== Gate FAILED：母项目 ${MOTHER_PASSED} passed 低于下限 ${MOTHER_FLOOR}（只升不降）==="
    exit 1
fi

# ---- vendor：域内失败只记录，不阻塞 ----
if [ "$VENDOR_RC" != "0" ]; then
    echo "（vendor 域内失败不阻塞母项目；已知问题见 scripts/gate-exclusions.txt / 子项目 Issue）"
fi
if [ -n "$VENDOR_PASSED" ] && [ "$VENDOR_FLOOR" -gt 0 ] && [ "$VENDOR_PASSED" -lt "$VENDOR_FLOOR" ]; then
    echo "（vendor 通过数 ${VENDOR_PASSED} 低于记录下限 ${VENDOR_FLOOR}，请核实）"
fi

rm -rf "$BASETEMP" 2>/dev/null || true

echo ""
echo "=== Gate passed：母项目 ${MOTHER_PASSED:-?} passed（下限 ${MOTHER_FLOOR}）；vendor ${VENDOR_PASSED:-?} passed（记录，下限 ${VENDOR_FLOOR}）==="
