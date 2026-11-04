"""T54 预跑照出来的三个工具缺陷：修复本体 + 每条一个"没有它就会红/不会红"的测试。

背景：NEC 上用现网数据副本跑 `m3 verify`（2026-10-30 14:35，盘中），
默认路径给的是 `红 2 / 钱类红 ⇒ 立即回退`，而那条线根本没坏。
切换日的第一小时清单正好也在盘中跑 ⇒ 这三条必须在切换前修掉。

工具的全部只读性也在这里钉住：任何检查不许写库。
"""
import sqlite3
from datetime import date, datetime

import pytest

from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.models import TradingConfig
from paper_trading.utils import m3_verify as mv

TODAY = date.today().isoformat()
PAST = "2026-05-08"


def at(hhmm: str) -> datetime:
    """把"今天几点几分"钉在 `TODAY` 上（T53 的日期敏感性教训）。

    这三条用例原来写死 `datetime(2026, 10, 30, …)`，而 `today` 取的是运行当天：
    跨过 10-30 以后 `check_nav/check_parity` 的"未到点"分支再也不成立，
    11-03 一跑就是三条红（现网与本地同时复现）。时刻属于"今天"，
    日期就必须与 `today` 同源——否则测的是"历史某天下午两点半"，不是这条闸。
    """
    hh, mm = hhmm.split(":")
    return datetime.fromisoformat(TODAY).replace(hour=int(hh), minute=int(mm))


@pytest.fixture()
def broker(tmp_path):
    return PaperBroker(db_path=str(tmp_path / "acct.db"),
                       config=TradingConfig(initial_cash=100_000.0))


@pytest.fixture()
def conn(broker):
    c = sqlite3.connect(str(broker.db_path))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def add_nav(c, day, cash=60000.0, mvv=40000.0, total=None):
    c.execute("""INSERT INTO nav_history (timestamp, cash, market_value, total_value,
                 available_cash, pnl, pnl_pct) VALUES (?,?,?,?,?,?,?)""",
              (f"{day}T15:30:00", cash, mvv,
               total if total is not None else cash + mvv, cash, 0.0, 0.0))
    c.commit()


def add_op(c, action="parity:diff", day=PAST, ok=1, result=None):
    import json
    c.execute("""INSERT INTO op_log (timestamp, action, params, ok, result,
                 cash_after, total_value_after) VALUES (?,?,?,?,?,?,?)""",
              (f"{day}T15:45:00", action, None, ok,
               json.dumps(result or {"cash_delta": 0.0, "fee_delta": 0.0,
                                     "fill_delta": 0}), None, None))
    c.commit()


# ---------------------------------------------------------------- ① 收盘 NAV 未到点
def test_nav_before_close_window_is_warn_not_money_red(conn):
    add_nav(conn, PAST)                              # 有历史 NAV，只缺"今天"
    r = mv.check_nav(conn, TODAY, now=at("14:35"))
    assert r["status"] == "warn", r
    assert "尚未到点" in r["detail"] or "收盘窗口未过" in r["detail"], r["detail"]
    assert r["money"] is True                        # 仍标钱类，但状态不是红


def test_nav_after_close_window_missing_is_red(conn):
    """过了收盘窗口还没有 ⇒ 真断供，必须红（这条不许被上面的放宽吞掉）。"""
    add_nav(conn, PAST)
    r = mv.check_nav(conn, TODAY, now=at("18:30"))
    assert r["status"] == "red" and r["money"] is True


def test_nav_missing_for_past_day_is_red_even_at_noon(conn):
    """查历史日：不存在"未到点"可言，中午查也要红。"""
    add_nav(conn, "2026-05-07")
    r = mv.check_nav(conn, "2026-05-08", now=datetime(2026, 10, 30, 10, 0))
    assert r["status"] == "red"


def test_verify_all_no_rollback_from_intraday_nav(broker, conn):
    """整体结论层面：盘中缺 NAV 不该把 `rollback_required` 点亮（否则指挥人回退）。"""
    add_nav(conn, PAST)
    out = mv.verify_all(broker=broker, data_db=None, symbols=[], today=TODAY,
                        cfg=TradingConfig(), now=at("14:35"))
    assert "nav" not in out["money_reds"], out["money_reds"]
    assert [i["id"] for i in out["items"] if i["id"] == "nav" and i["status"] == "red"] == []


# ---------------------------------------------------------------- ② 影子留痕未到点
def test_parity_before_shadow_time_does_not_accuse_the_cron(conn):
    r = mv.check_parity_trace(conn, TODAY, now=at("14:35"))
    assert r["status"] == "warn"
    assert "时点未到" in r["detail"], r["detail"]
    assert "没跑" not in r["detail"], "未到点时不许指控 cron"


def test_parity_after_shadow_time_uses_the_old_wording(conn):
    r = mv.check_parity_trace(conn, TODAY, now=at("16:10"))
    assert r["status"] == "warn" and "cron 没跑" in r["detail"]
    r2 = mv.check_parity_trace(conn, PAST)           # 历史日：直接旧文案
    assert "cron 没跑" in r2["detail"]


def test_parity_unattributed_delta_is_still_red(conn):
    add_op(conn, day=PAST, result={"cash_delta": 0.0, "fee_delta": 3.5, "fill_delta": 0})
    r = mv.check_parity_trace(conn, PAST)
    assert r["status"] == "red"


# ---------------------------------------------------------------- ③ 心跳路径
def test_heartbeat_file_missing_is_skip_not_red(tmp_path):
    """读不到证据 ≠ 线挂了：报 skip 并把试过的路径写出来（T54 实测的假红就是这条）。"""
    r = mv.check_heartbeat(path=tmp_path / "nope.json")
    assert r["status"] == "skip" and "找不到心跳文件" in r["detail"]
    r2 = mv.check_heartbeat(cwd=tmp_path / "empty", root=tmp_path / "empty-root")
    assert r2["status"] == "skip"
    assert str(tmp_path / "empty" / "var" / "heartbeat.json") in r2["detail"]


def test_heartbeat_resolves_repo_root_when_cwd_lacks_it(tmp_path):
    """操作者在 `~`/scratch 里跑：回退到仓库根的 var/heartbeat.json（cron 的 cwd 就是仓库根）。"""
    import json
    from pathlib import Path

    cwd, root = tmp_path / "cwd", tmp_path / "root"
    (root / "var").mkdir(parents=True)
    hb = root / "var" / "heartbeat.json"
    hb.write_text(json.dumps({"engine": {"ts": "2026-10-30T14:35:00", "ok": True},
                              "agent": {"ts": "2026-10-30T09:30:00", "ok": True}}),
                  encoding="utf-8")
    resolved, tried = mv.resolve_heartbeat_path(cwd=cwd, root=root)
    assert resolved == hb and len(tried) == 2
    r = mv.check_heartbeat(cwd=cwd, root=root, now=datetime(2026, 10, 30, 14, 36))
    assert r["status"] in ("green", "red")            # 读到文件才谈得上判定
    assert str(hb) in r["detail"]                     # 且把用了哪个文件写出来


def test_check_heartbeat_explicit_path_still_wins(tmp_path):
    import json
    p = tmp_path / "hb.json"
    p.write_text(json.dumps({"engine": {"ts": "2026-10-30T14:30:00", "ok": True}}),
                 encoding="utf-8")
    resolved, _ = mv.resolve_heartbeat_path(p, cwd=tmp_path, root=tmp_path)
    assert resolved == p


# ---------------------------------------------------------------- ④ 工具仍然只读
def test_verify_all_is_read_only(broker, conn):
    """全套跑完后账户库的字节不变：切换日要对着现网库跑，不许顺手写。"""
    add_nav(conn, PAST)
    before = broker.db_path.read_bytes()
    mv.verify_all(broker=broker, data_db=None, symbols=[], today=TODAY,
                  cfg=TradingConfig(), now=at("14:35"))
    assert broker.db_path.read_bytes() == before


# ---------------------------------------------------------------- ⑤ 池来源要看得见
def test_format_text_shows_pool_source():
    """从 `~` 跑时 cwd 没 config.yaml ⇒ 池为空；不写来源就会被误读成"没什么要查的"。"""
    res = {"today": "2026-10-30", "items": [mv.item("raw_gate", "行情口径与新鲜度闸",
                                                   "skip", "股票池为空")],
           "green": 0, "warn": 0, "red": 0, "skipped": 1, "pool_source": "config: 未读到",
           "rollback_required": False, "money_reds": [], "on_red": {}}
    text = mv.format_text(res)
    assert "池 config: 未读到" in text, text
    assert "跳 1" in text
