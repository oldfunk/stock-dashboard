#!/bin/sh
# T80（M4-②）+ T78（M4-③）：每日核对的**真身**在生产机上跑。
# CI 那层跑的是纯逻辑契约与门禁自检（report-t78 §4 说明为什么四场景必须在生产机）。
#
# 装法（运维/主 AI 执行，我没有动 crontab）：
#   30 15 * * 1-5 /home/nec/paper-trading/deploy/daily-check.sh >> /home/nec/paper-trading/logs/daily_check.log 2>&1
# 15:30 在收盘后、旧线 `agent tick` 的收盘窗口之前。退出码就是结论，与 `m3 verify`
# 同一把尺子：0=绿/黄，1=有红，3=钱类红（§5.6 的"立即回退"评估线）。
# M4 验收口径①（连续 5 个交易日无未归因异常）读的就是这张表。
#
# 全程只读：核对项一律 mode=ro 开库；四场景用 data.db 的**副本** + 临时目录里
# 现造的账本副本，`ledger=False` ⇒ 不写 paper_account.db、不写 op_log、
# 不碰 var/rq_state。想只跑核对不跑四场景：PT_RUN_SCENES=0。
set -u
# PT_ROOT/PT_PY 是给"部署前彩排"留的口子（我用候选树+同一把尺子跑一遍），
# 默认值就是现网路径。
ROOT=${PT_ROOT:-/home/nec/paper-trading}
PY=${PT_PY:-$ROOT/venv/bin/python}
ENG=${PT_ENGINE_PY:-/home/nec/venvs/rq-engine/bin/python}
BUNDLE=${PT_RQ_BUNDLE:-/home/nec/.rqalpha/bundle}
# 库路径默认就是现网那两本；彩排时可以指到别处（只读打开，不写）。
DATA_DB=${PT_DATA_DB:-$ROOT/data.db}
ACCT_DB=${PT_ACCOUNT_DB:-$ROOT/paper_account.db}
OUT=$ROOT/logs/daily_check.jsonl
ERR=$ROOT/logs/daily_check.err
mkdir -p "$ROOT/logs"
cd "$ROOT" || exit 1
TODAY=$(date +%F)

emit_line() {  # emit_line <raw-json-file> <kind> <exit-code>
  $PY -c '
import datetime, json, sys
raw = open(sys.argv[1], encoding="utf-8").read()
i = raw.find("{")
try:
    obj = json.loads(raw[i:]) if i >= 0 else None
except json.JSONDecodeError:
    obj = None
if not isinstance(obj, dict):
    sys.stderr.write("daily-check: 没解出 JSON（%s）\n" % sys.argv[1])
    sys.exit(2)
data = obj.get("data", obj) if isinstance(obj, dict) else {}
rec = {"kind": sys.argv[2],
       "at": datetime.datetime.now().isoformat(timespec="seconds"),
       "exit_code": int(sys.argv[3]), "today": sys.argv[4]}
if sys.argv[2] == "daily_check":
    for k in ("green", "warn", "red", "skipped", "rollback_required", "ok"):
        rec[k] = data.get(k)
    items = data.get("items") or []
    rec["red_ids"] = [i["id"] for i in items if i.get("status") == "red"]
    rec["warn_ids"] = [i["id"] for i in items if i.get("status") == "warn"]
else:
    rec["scenes"] = data.get("scenes")
    rec["guard"] = data.get("guard_stale_state")
print(json.dumps(rec, ensure_ascii=False))
' "$1" "$2" "$3" "$TODAY" >> "$OUT"
}

# ---- 1) 每日核对（m3 verify 全套 + 框架对齐/顺延落地/意图全链）
RC=0
RAW=$(mktemp)
$PY -m paper_trading.cli --data-db "$DATA_DB" --account-db "$ACCT_DB" \
     m3 daily-check --json --today "$TODAY" > "$RAW" 2>>"$ERR" || RC=$?
emit_line "$RAW" daily_check "$RC" || RC=3
rm -f "$RAW"
echo "[daily-check] $TODAY daily_check exit=$RC"

# ---- 2) init_positions 四场景（A 冻结拒 / B 超支拒 / C 卖成交 / D 买成交 + 脱钩拒跑）
# 这层只有装了 rqalpha、有 bundle、有当日分钟线的机器上跑得动 —— 就是这台。
SC_RC=0
if [ "${PT_RUN_SCENES:-1}" = "1" ] && [ -x "$ENG" ] && [ -d "$BUNDLE" ]; then
  TMPD=$(mktemp -d)
  # 一致快照（T81 P2-5）：`cp` 一个正在被写的库会拿到半截页，四场景据此跑出的
  # 绿/红都不可信。VACUUM INTO 由 sqlite 自己保证一致，源库以 mode=ro 打开。
  if $PY -c 'import sqlite3, sys
c = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
c.execute("VACUUM INTO ?", (sys.argv[2],))
c.close()' "$DATA_DB" "$TMPD/data_copy.db" 2>>"$ERR"; then
    $PY tests/test_init_positions_e2e.py --data-db "$TMPD/data_copy.db" \
       --bundle "$BUNDLE" --python "$ENG" --workdir "$TMPD/scenes" \
       --out "$TMPD/scenes.json" --today "$TODAY" >>"$ERR" 2>&1 || SC_RC=$?
  else
    echo "daily-check: data.db 一致快照失败（VACUUM INTO），四场景不跑" >> "$ERR"
    SC_RC=3
  fi
  if [ -s "$TMPD/scenes.json" ]; then
    emit_line "$TMPD/scenes.json" scenes "$SC_RC" || SC_RC=3
  else
    echo '{"kind":"scenes","today":"'"$TODAY"'","exit_code":3,"scenes":null,
           "why":"四场景没出 JSON（引擎 venv/bundle/当日分钟线缺失？）"}' >> "$OUT"
    SC_RC=3
  fi
  rm -rf "$TMPD"
  echo "[daily-check] $TODAY scenes exit=$SC_RC"
else
  echo "[daily-check] 四场景未跑（PT_RUN_SCENES=${PT_RUN_SCENES:-1}/引擎 venv/bundle 缺失）"
fi

if [ "$RC" -ge "$SC_RC" ]; then FINAL=$RC; else FINAL=$SC_RC; fi
if [ "$FINAL" -ge 3 ]; then
  echo "[daily-check] 钱类红：按 migration-m3 §5.6 评估回退；详情 tail -1 $OUT"
fi
exit "$FINAL"
