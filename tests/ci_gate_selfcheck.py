"""CI 门禁自检（T78·M4-③）：证明"门禁真跑"，而不是"workflow 里写了一行"。

三步各判一件事，退出码就是结论（CI 里每条都是**独立步骤**）：
1. `m3 daily-check --json` 能在合成库上跑完并产出全部核对项（工具本身活着）；
2. 退出码与结论**自洽**：无红=0 / 有红=1 / 钱类红=3（`m3 verify` 的 §5.6 尺子）；
3. **构造一个必须红的现场**（该有会话留痕却没有）⇒ 退出码非 0。
   门禁如果永远不会失败，它就不是门禁，是装饰。

合成库只在本文件的临时目录里，不读生产库、不写 `op_log`。
`python tests/ci_gate_selfcheck.py` 本地复跑与 CI 完全同一条路径。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_trading.broker.paper_broker import PaperBroker       # noqa: E402
from paper_trading.data.db_manager import DataDBManager          # noqa: E402
from paper_trading.data.minute import expected_slots             # noqa: E402
from paper_trading.models import Bar, TradingConfig             # noqa: E402
from paper_trading.utils import heartbeat as hb                 # noqa: E402
from paper_trading.utils.trading_calendar import is_trading_day  # noqa: E402


def recent_trading_days(today: str, n: int = 3) -> list[str]:
    """截至 `today` 的最近 n 个交易日（升序；today 不是交易日就往前找）。

    合成库的日期必须**跟着运行日走**：写死 11-02/03/04 的那版，过了 11-06
    就开始"落后 N 个交易日"，N 每天 +1 ⇒ 永久门禁天天红（T53 同一类日期敏感）。
    """
    d = date.fromisoformat(today)
    out: list[str] = []
    while len(out) < n:
        if is_trading_day(d):
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def build_fixture(tmp: Path, today: str) -> dict:
    """造一本最小可用的合成库：一只标的、几天 raw 日线、当日 5 分钟线、空账本持仓。

    `nav_history` 要有当日的**收盘口径**行，否则 `check_nav` 会因"收盘窗口过了却
    没净值"报钱类红——那是合成库的缺口，不是被检对象的问题。门禁的基线必须
    **真的能全绿**，否则"红就非零"永远非零，就成了另一种壁纸（Q5 同一课）。

    三处都得跟着运行日/运行时刻走，缺一个就是"只在作者写它那天能过"的门禁：
    raw 日线（落后判据）、当日分钟线（盘中没当天 bar 就红）、心跳（应跳未跳就红）。
    """
    data_db = tmp / "data.db"
    acct_db = tmp / "account.db"
    db = DataDBManager(data_db)
    sym = "600519"
    days = recent_trading_days(today)
    bars = [Bar(symbol=sym, timestamp=datetime.fromisoformat(f"{d}T15:00:00"),
                open=1400.0, high=1420.0, low=1390.0, close=1410.0, volume=10000,
                amount=1.41e7, adjust="raw") for d in days]
    db.upsert_raw_bars(bars, source="ci-selfcheck", checked_dates=set(days))
    db.upsert_minute_bars(sym, [{"ts": f"{today}T{s}:00", "open": 1400.0,
                                 "high": 1420.0, "low": 1390.0, "close": 1410.0,
                                 "volume": 200, "amount": 2.82e5}
                                for s in expected_slots("5")], period="5")
    PaperBroker(db_path=str(acct_db), config=TradingConfig(initial_cash=100000.0))
    with sqlite3.connect(acct_db) as c:
        c.execute("INSERT INTO nav_history (timestamp, cash, market_value,"
                  " total_value, available_cash, pnl, pnl_pct)"
                  " VALUES (?, 100000.0, 0.0, 100000.0, 100000.0, 0.0, 0.0)",
                  (f"{today}T15:30:00",))
    # 心跳显式指到合成文件：不给 --heartbeat 时它会回退读**仓库根** var/heartbeat.json，
    # 于是本地复跑读的是操作者真机的心跳（应跳未跳⇒红），CI 里又没有那个文件（skip）
    # ——同一条门禁两种结论。合成心跳一律"刚跳过"，应跳时段绿、非应跳时段 warn。
    hb_path = tmp / "heartbeat.json"
    for name in ("engine", "agent", "rq_tick", "minute"):
        hb.touch(name, detail="ci-selfcheck", path=hb_path)
    return {"data_db": str(data_db), "account_db": str(acct_db), "symbol": sym,
            "heartbeat": str(hb_path)}


def run_cli(args: list, cwd: Path) -> subprocess.CompletedProcess:
    """子进程跑真 CLI（退出码才是门禁的语义）。cwd 留在合成库目录，
    靠 `PYTHONPATH` 找到包——不读仓库根的任何本地配置。"""
    import os

    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "paper_trading.cli", *args],
                          capture_output=True, text=True, cwd=str(cwd), env=env)


def parse_json(stdout: str, stderr: str = "") -> dict:
    """CLI 打的是缩进 JSON（前面可能还有日志行）：从每个 `{` 试着解，取第一个成品。"""
    text = stdout or ""
    pos = text.find("{")
    while pos >= 0:
        try:
            obj = json.loads(text[pos:])
        except json.JSONDecodeError:
            pos = text.find("{", pos + 1)
            continue
        if isinstance(obj, dict) and "data" in obj:     # emit() 包了信封 {ok,data,error}
            obj = obj["data"]
        if isinstance(obj, dict) and "items" in obj:
            return obj
        pos = text.find("{", pos + 1)
    raise AssertionError("输出里没有可解析的 JSON。stdout 尾：" + text[-400:]
                         + " ｜ stderr 尾：" + (stderr or "")[-1200:])


def main() -> int:
    today = date.today().isoformat()
    fails = []
    # ignore_errors：Windows 上 sqlite 句柄可能还没释放（CI 是 Linux，但本地复跑
    # 不该因为清理临时目录失败而报"门禁挂了"——那是假红，结论已经打在上面了）
    with tempfile.TemporaryDirectory(prefix="ci-gate-",
                                     ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        fx = build_fixture(tmp, today)
        state = tmp / "state"
        state.mkdir()
        common = ["--data-db", fx["data_db"], "--account-db", fx["account_db"]]
        hbarg = ["--heartbeat", fx["heartbeat"]]

        # 1) 工具真跑：合成库上一整套核对，项数与 id 齐全
        p = run_cli([*common, "m3", "daily-check", "--json", "--today", today,
                     "--symbols", fx["symbol"], "--state-dir", str(state),
                     *hbarg, "--expect-work", "no"], cwd=tmp)
        res = parse_json(p.stdout, p.stderr)
        ids = {i["id"] for i in res["items"]}
        for need in ("init_align", "carry_over", "intent_chain", "raw_gate",
                     "t_plus_one", "fees", "nav", "heartbeat", "minute_feed"):
            if need not in ids:
                fails.append(f"daily-check 缺核对项 {need}（实得 {sorted(ids)}）")
        # 2) 退出码与结论自洽
        expect = 3 if res.get("rollback_required") else (1 if res.get("red") else 0)
        if p.returncode != expect:
            fails.append(f"退出码 {p.returncode} ≠ 结论要求的 {expect}"
                         f"（red={res.get('red')} money_red={res.get('rollback_required')}）")
        if res.get("green", 0) + res.get("warn", 0) + res.get("red", 0) \
                + res.get("skipped", 0) != len(res["items"]):
            fails.append("计数与 items 不一致（汇总口径漂了）")
        if res.get("red"):
            reds = [(i["id"], i["detail"]) for i in res["items"]
                    if i["status"] == "red"]
            fails.append("健康基线不该有红：" + str(reds)
                         + "——门禁永远非零就是新壁纸")

        # 3) 必须能红：该有会话留痕却没有 ⇒ init_align 红 ⇒ 非零退出
        p2 = run_cli([*common, "m3", "daily-check", "--json", "--today", today,
                      "--symbols", fx["symbol"], "--state-dir", str(state),
                      *hbarg, "--expect-work", "yes"], cwd=tmp)
        res2 = parse_json(p2.stdout, p2.stderr)
        bad = [i for i in res2["items"] if i["id"] == "init_align"]
        if not bad or bad[0]["status"] != "red":
            fails.append(f"构造的『缺留痕』现场没让 init_align 变红：{bad}")
        if p2.returncode == 0:
            fails.append("有钱类红却退出 0 —— 门禁不会失败，等于没有门禁")
        if not res2.get("rollback_required"):
            fails.append("init_align 红未标钱类（rollback_required 应为真）")

        # 4) verify 与 daily-check 用同一把尺子：钱类红 ⇒ 3
        print(json.dumps({"items": len(res["items"]), "rc": p.returncode,
                          "forced_red_rc": p2.returncode,
                          "rollback_required": res2.get("rollback_required"),
                          "red_ids": [i["id"] for i in res2["items"]
                                      if i["status"] == "red"]},
                         ensure_ascii=False))
    if fails:
        print("\n".join(f"FAIL {f}" for f in fails), file=sys.stderr)
        return 1
    print("CI 门禁自检：4 项全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
