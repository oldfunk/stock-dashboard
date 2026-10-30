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
# 失败/中断也清理 basetemp：/tmp 是 478M tmpfs，历史遗留会撑爆磁盘
#（2026-10-26 实测：门禁失败时提前 exit，BASETEMP 未清理，/tmp 98% 满导致后续门禁全 ERROR）
cleanup_basetemp() { rm -rf "$BASETEMP" 2>/dev/null || true; }
trap cleanup_basetemp EXIT INT TERM
# 清理遗留的 pytest 临时目录：按目录名里的 PID 判活，**只删已死进程的**。
# 旧实现是通配删 pytest-gate-*——两条 gate 同机并发时（母方验收 + 员工自检）
# 会抽掉对方正在用的 basetemp，对方表现为整批偶发 ERROR，现场极难归因。
# tmpfs 撑爆那条真风险已由上面的 trap 解决，不需要通配删来兜。
# 判活失败只会留下残目录（可下次再清），不会误删在用目录——取这个方向。
for _stale in "${LOCALAPPDATA:-/tmp}"/pytest-gate-*; do
    [ -d "$_stale" ] || continue
    _pid="${_stale##*-}"
    case "$_pid" in (''|*[!0-9]*) continue ;; esac
    kill -0 "$_pid" 2>/dev/null && continue
    rm -rf "$_stale" 2>/dev/null || true
done

# 优先用项目 venv（生产服务器），否则回退系统 python
PYBIN="${PYTHON:-}"
if [ -z "$PYBIN" ]; then
    if [ -x ".venv/bin/python" ]; then PYBIN=".venv/bin/python";
    elif command -v python3 >/dev/null 2>&1; then PYBIN="python3";
    else PYBIN="python"; fi
fi

# 超时保护（2026-10-24）：挂死用例从“无限阻塞”变为“限时失败”（fail-closed）。
# 背景：v0.6.7 曾有 vendor 用例打真实网络挂死，无超时会卡死整条部署；
# 2026-10-24 母套件也出现过一次偶发挂起（akshare 真实调用），故母套件 600s 上限（正常约 230s）。
# 只认 GNU coreutils 的 timeout（Windows 自带 timeout.exe 语法不同，误用会破坏本机语法检查）。
TIMEOUT_BIN=""
if timeout --version >/dev/null 2>&1; then TIMEOUT_BIN="timeout"; fi
MOTHER_TIMEOUT="${GATE_TIMEOUT_MOTHER:-600}"
VENDOR_TIMEOUT="${GATE_TIMEOUT_VENDOR:-600}"

# vendor 已知问题排除清单（带 Issue 链接；修复后移出并恢复基线）
DESELECT=()
if [ -f scripts/gate-exclusions.txt ]; then
    while IFS= read -r _line; do
        _line="${_line%%#*}"
        _line="$(printf '%s' "$_line" | tr -d ' \r')"
        [ -n "$_line" ] && DESELECT+=(--deselect "$_line")
    done < scripts/gate-exclusions.txt
fi

_floor() {  # $1=基线文件；取**首个纯数字行**作下限，其余内容一律当注释忽略
    # 旧实现 `tr -dc '0-9'` 会把文件里所有数字串成一个数：基线文件一旦被人
    # 加一行日期注释（如 "# 2026-10-27 由 856 升 875"），下限就变成
    # 202610278561875，此后每次部署都 fail-closed，而报错写的是
    # "passed 低于下限"——看着像测试挂了，实际是门禁自己被读错了（审计 N14）。
    local f=0
    if [ -f "$1" ]; then
        f=$(grep -m1 -E '^[[:space:]]*[0-9]+[[:space:]]*$' "$1" | tr -dc '0-9')
    fi
    printf '%s' "${f:-0}"
}

_run() {  # $1=标签 $2=超时秒数；其余=pytest 参数
    local label="$1" limit="$2"; shift 2
    local rc outfile
    # 注意：输出文件必须放在 BASETEMP 之外——pytest 启动时会清空 --basetemp 目录，
    # 放在里面会被删掉（2026-10-26 实测 out.log 消失、gate 误判失败）。
    outfile="${TMPDIR:-/tmp}/gate-${label}-$$.out"
    local cmd=("$PYBIN" -m pytest --tb=short -q --basetemp="$BASETEMP" "$@")
    if [ -n "$TIMEOUT_BIN" ]; then cmd=("$TIMEOUT_BIN" "$limit" "${cmd[@]}"); fi
    # 2026-10-26：不要用 $(...) 收集输出——pytest 被 timeout 杀掉后，遗留子进程
    # 可能仍持有管道写端，命令替换永远等不到 EOF，整个 deploy 挂死（已实测 25min+）。
    # 重定向到文件则父进程退出即可返回。
    "${cmd[@]}" > "$outfile" 2>&1
    rc=$?
    out=$(cat "$outfile")
    # 保留完整输出，失败时可追溯（2026-10-25：偶发 ERROR 因只留 tail -3 无法定位）。
    # 文件名带 PID（批6-a N14b）：两条 gate 同机并发时（母方验收 + 员工自检）固定名
    # `-last.log` 会被后写的那条覆掉，前一条的取证现场直接消失。
    # 只在失败时保留、成功即删除——nec 的 /tmp 是 478M tmpfs，不能靠堆日志。
    # `.out`（本次原始输出）与 `-last.log`（失败取证副本）同理：失败两份都留着，
    # 成功两份都不留（批11-b：此前只删 last.log，`.out` 每次运行都留一个文件）。
    GATE_LOG="${TMPDIR:-/tmp}/gate-${label}-$$-last.log"
    if [ "$rc" != "0" ]; then
        cp "$outfile" "$GATE_LOG" 2>/dev/null || true
    else
        rm -f "$GATE_LOG" 2>/dev/null || true
        rm -f "$outfile" 2>/dev/null || true
    fi
    if [ "$rc" = "124" ]; then
        echo "!!! $label 套件超时（>${limit}s，timeout 终止）——视为失败"
    fi
    if [ "$rc" != "0" ]; then
        echo "!!! $label 失败详情（完整日志 $GATE_LOG）："
        printf '%s\n' "$out" | tail -60
    fi
    printf '%s\n' "$out" | tail -3
    printf '%s\n' "$out" | grep -oE '[0-9]+ passed' | head -1 | awk '{print $1}' > "$BASETEMP/$label.count"
    printf '%s' "$rc" > "$BASETEMP/$label.rc"
}

echo "=== 母项目门禁（权威：排除 paper_trading/）==="
echo "（超时保护：母 ${MOTHER_TIMEOUT}s / vendor ${VENDOR_TIMEOUT}s；timeout=${TIMEOUT_BIN:-无}）"
_run mother "$MOTHER_TIMEOUT" --ignore=paper_trading
MOTHER_PASSED=$(cat "$BASETEMP/mother.count" 2>/dev/null)
MOTHER_RC=$(cat "$BASETEMP/mother.rc" 2>/dev/null)
MOTHER_FLOOR=$(_floor scripts/gate-baseline.txt)

echo "=== vendor 套件（记录，不阻塞母项目）==="
_run vendor "$VENDOR_TIMEOUT" paper_trading/tests ${DESELECT[@]+"${DESELECT[@]}"}
VENDOR_PASSED=$(cat "$BASETEMP/vendor.count" 2>/dev/null)
VENDOR_RC=$(cat "$BASETEMP/vendor.rc" 2>/dev/null)
VENDOR_FLOOR=$(_floor scripts/gate-baseline-vendor.txt)

# ---- 判定：母项目必须全绿且不低于下限 ----
if [ "$MOTHER_RC" != "0" ]; then
    echo ""
    echo "=== Gate FAILED：母项目用例失败（exit $MOTHER_RC）==="
    exit "$MOTHER_RC"
fi
# 通过数为空 = 无法解析结果（收集异常/输出异常）→ fail-closed，不得当成功
if [ -z "$MOTHER_PASSED" ]; then
    echo ""
    echo "=== Gate FAILED：母项目通过数无法解析（不能验证下限，fail-closed）==="
    exit 1
fi
if [ "$MOTHER_PASSED" -lt "$MOTHER_FLOOR" ]; then
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
