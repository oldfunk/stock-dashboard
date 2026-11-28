#!/usr/bin/env bash
# v0.12 发布门禁：一条命令跑完全部可自动判定项，输出 GO / NO-GO。
#
# 用法：
#   bash deploy/release-gate.sh                # 含全量 pytest
#   bash deploy/release-gate.sh --skip-tests   # 赶时间/已单独跑过
#   bash deploy/release-gate.sh --root /home/nec/paper-trading
#
# 退出码：0=GO；1=有红；3=钱类/母库契约红（优先处理）。
# 只读检查 + 生成备份；不改策略、不下单。
set -u

ROOT="/home/nec/paper-trading"
SKIP_TESTS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-tests) SKIP_TESTS=1 ;;
    --root) ROOT="${2:-$ROOT}"; shift ;;
    -h|--help) sed -n '1,18p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done

PY="${PT_PY:-$ROOT/venv/bin/python}"
DATA_DB="${PT_DATA_DB:-$ROOT/data.db}"
ACCT_DB="${PT_ACCOUNT_DB:-$ROOT/paper_account.db}"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
STAMP=$(date +%Y%m%dT%H%M%S)
LOG="$LOG_DIR/release-gate-$STAMP.log"
exec > >(tee -a "$LOG") 2>&1

FAIL=0
MONEY=0
step() {  # step <label> <command...>
  local label="$1"; shift
  echo ""
  echo "=== $label ==="
  if "$@"; then
    echo "[PASS] $label"
  else
    local rc=$?
    if [ "$rc" -eq 3 ]; then MONEY=1; fi
    echo "[FAIL] $label (exit=$rc)"
    FAIL=1
  fi
}

cd "$ROOT" || { echo "no such root: $ROOT" >&2; exit 2; }
echo "release-gate $STAMP root=$ROOT"
echo "account_db=$ACCT_DB data_db=$DATA_DB"

# 0) 钱类快照（只记录，不判红；发布前后人工对照）
echo ""
echo "=== money snapshot (pre) ==="
"$PY" - "$ACCT_DB" <<'PY'
import json, sqlite3, sys
p = sys.argv[1]
c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
def one(sql):
    r = c.execute(sql).fetchone()
    return r[0] if r else None
print(json.dumps({
    "cash": one("SELECT cash FROM account WHERE id=1"),
    "initial_cash": one("SELECT initial_cash FROM account WHERE id=1"),
    "position_volume": one("SELECT COALESCE(SUM(total_volume),0) FROM positions"),
    "position_cost": one("SELECT COALESCE(SUM(avg_cost*total_volume),0) FROM positions"),
    "fills": one("SELECT COUNT(*) FROM fills"),
    "nav": one("SELECT COUNT(*) FROM nav_history"),
    "nav_last": one("SELECT total_value FROM nav_history ORDER BY id DESC LIMIT 1"),
}, ensure_ascii=False, sort_keys=True))
PY

# 1) 全量测试
if [ "$SKIP_TESTS" = "0" ]; then
  step "pytest (full)" "$PY" -m pytest -q --timeout=180 -p no:cacheprovider
else
  echo ""; echo "=== pytest (skipped) ==="
fi

# 2) 运维巡检（备份/心跳/raw_gate/qfq/熔断）
step "ops check" "$PY" -m paper_trading.cli ops check --json

# 3) 账本备份状态 + 生成一份发布前备份
step "ledger backup" "$PY" -m paper_trading.cli ledger backup \
  --out-dir "$ROOT/backups" --keep 20 --json
step "ledger backup-status" "$PY" -m paper_trading.cli ledger backup-status \
  --out-dir "$ROOT/backups" --max-age-hours 26 --json

# 4) 数据门禁
step "data contract" "$PY" -m paper_trading.cli data contract --json
step "data raw-coverage --gate" "$PY" -m paper_trading.cli data raw-coverage \
  --gate --json
step "data qfq-check (red blocks)" "$PY" -m paper_trading.cli data qfq-check --json

# 5) 收盘链核对（m3 daily-check；0=绿/黄，1=红，3=钱类红）
step "m3 daily-check" "$PY" -m paper_trading.cli m3 daily-check --json

# 6) 面板三接口（面板未启动则 skip，不判红）
if command -v curl >/dev/null 2>&1; then
  echo ""; echo "=== panel endpoints ==="
  for u in /api/ai-scorecard /api/strategy-allocations /api/ops-status \
           "/api/report?kind=daily&date=$(date +%F)" \
           "/api/audit?date=$(date +%F)"; do
    code=$(curl -s -o /tmp/pt-gate.json -w '%{http_code}' --max-time 10 \
      "http://127.0.0.1:8080$u" || echo 000)
    if [ "$code" = "200" ]; then
      echo "[PASS] $u ($code)"
    else
      echo "[WARN] $u ($code)（面板未启动或端口不同）"
    fi
  done
else
  echo ""; echo "=== panel endpoints (curl missing, skipped) ==="
fi

# 7) 结论
echo ""
echo "=== money snapshot (post) ==="
"$PY" - "$ACCT_DB" <<'PY'
import sqlite3, sys
p = sys.argv[1]
c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
print("cash", c.execute("SELECT cash FROM account WHERE id=1").fetchone()[0],
      "positions", c.execute("SELECT COUNT(*) FROM positions").fetchone()[0],
      "fills", c.execute("SELECT COUNT(*) FROM fills").fetchone()[0],
      "nav", c.execute("SELECT COUNT(*) FROM nav_history").fetchone()[0])
PY
if [ "$MONEY" = "1" ]; then
  echo "=== NO-GO（钱类/契约红，先按事故流程处理） ==="
  exit 3
elif [ "$FAIL" = "1" ]; then
  echo "=== NO-GO（有红项，见上） ==="
  exit 1
else
  echo "=== GO（允许黄项，但需在发布记录中写清） ==="
  exit 0
fi
