"""AI 定时队列单测（纯函数 + 本地文件往返 + 分发集成，全离线）。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent import schedule as sch  # noqa: E402


def test_parse_entry():
    assert sch.parse_entry("16:45") == {"time": "16:45", "action": "trade"}
    assert sch.parse_entry("9:5=sync") == {"time": "09:05", "action": "sync"}
    assert sch.parse_entry({"time": "13:00", "action": "PLAN"}) == {"time": "13:00",
                                                                    "action": "plan"}
    for bad in ["", "25:00", "16:60", "16:45=bomb", "16-45", 123, None]:
        with pytest.raises(ValueError):
            sch.parse_entry(bad)


def test_parse_queue_rules():
    q = sch.parse_queue(["17:30=trade", "09:00=sync"])
    assert [e["time"] for e in q] == ["09:00", "17:30"]  # 自动排序
    with pytest.raises(ValueError):  # 同一时刻两条打架
        sch.parse_queue(["16:45=sync", "16:45=trade"])
    with pytest.raises(ValueError):  # 空队列
        sch.parse_queue([])
    with pytest.raises(ValueError):  # 超长
        sch.parse_queue([f"{10 + i:02d}:00=sync" for i in range(6)])


def test_save_load_roundtrip(tmp_path):
    ok, msg = sch.save_schedule(tmp_path, ["09:00=sync", "16:45=trade"])
    assert ok, msg
    cur = sch.load_schedule(tmp_path)
    assert cur["entries"] == [{"time": "09:00", "action": "sync"},
                              {"time": "16:45", "action": "trade"}]
    assert cur["source"] == "local"


def test_load_defaults_and_legacy(tmp_path):
    assert sch.load_schedule(tmp_path) == {
        "entries": [{"time": "16:45", "action": "trade"}], "source": "default"}
    # 旧 slots 列表迁移为 trade
    (tmp_path / "strategy.local.yaml").write_text(
        "agent_schedule:\n  slots: ['16:45']\n  max_runs: 3\n", encoding="utf-8")
    cur = sch.load_schedule(tmp_path)
    assert cur["entries"] == [{"time": "16:45", "action": "trade"}]


def test_save_rejects_bad_and_keeps_old(tmp_path):
    assert sch.save_schedule(tmp_path, ["16:45=trade"])[0]
    assert not sch.save_schedule(tmp_path, ["99:99=trade"])[0]
    assert not sch.save_schedule(tmp_path, ["16:45=bomb"])[0]
    assert sch.load_schedule(tmp_path)["entries"] == [{"time": "16:45",
                                                       "action": "trade"}]


def test_check_queue_order():
    at = lambda hm: datetime(2026, 9, 29, int(hm[:2]), int(hm[3:]))
    q = [{"time": "09:00", "action": "sync"},
         {"time": "13:00", "action": "plan"},
         {"time": "16:45", "action": "trade"}]
    assert sch.check(q, set(), at("08:00"))[0] is False
    ok, entry, _ = sch.check(q, set(), at("10:00"))
    assert (ok, entry) == (True, {"time": "09:00", "action": "sync"})
    # 保序：09:00 跑完后，13:00 到点才放行 plan（不跳过直接跑 trade）
    ok, entry, _ = sch.check(q, {"09:00"}, at("14:00"))
    assert (ok, entry) == (True, {"time": "13:00", "action": "plan"})
    ok, _, reason = sch.check(q, {"09:00", "13:00", "16:45"}, at("18:00"))
    assert (ok, reason) == (False, "not-in-schedule")


def _bridge(tmp, bars):
    import tempfile

    import paper_trading.cli as hb

    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    b = hb.TradingBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                         secrets_path=str(Path(tmp) / "s.json"))
    b.data_db.upsert_bars(bars)
    b.llm_save("custom", "https://x/v1", "m", "k")  # fake 源，chat 由单测替换
    return b


def _fresh():
    from datetime import timedelta

    from paper_trading.models import Bar
    return [Bar(symbol="600519",
                timestamp=datetime(2026, 9, 29) + timedelta(days=10000 + i),
                open=10.0, high=11.0, low=9.0, close=10.0, volume=100)
            for i in range(30)]


def _mock_net(monkeypatch):
    from paper_trading.cli import TradingBridge
    monkeypatch.setattr(TradingBridge, "sync_data",
                        lambda self, symbols: {"ok": True, "symbols": symbols,
                                               "updated": {s: 1 for s in symbols}})


def test_tick_dispatches_each_action(monkeypatch, tmp_path):
    """tick 按动作分发：sync/plan/analyze/trade 各跑各的，都记 slot 标记。"""
    import json

    from paper_trading.agent import AgentTrader
    from paper_trading.llm import provider as prov

    _mock_net(monkeypatch)
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(
        {"actions": [], "summary": "不动"}))
    assert sch.save_schedule(tmp_path, ["09:00=sync", "13:00=plan",
                                        "16:00=analyze", "16:45=trade"])[0]
    b = _bridge(str(tmp_path), _fresh())
    t = AgentTrader(b)
    fired = []
    for hm in ["09:30", "13:30", "16:30", "17:30"]:
        monkeypatch.setattr(sch, "_now", lambda hm=hm: datetime(2026, 9, 29,
                                                              int(hm[:2]),
                                                              int(hm[3:])))
        r = t.tick(["600519"], sched_root=str(tmp_path))
        assert r.get("slot"), r
        fired.append(r["slot"])
    assert fired == ["09:00", "13:00", "16:00", "16:45"]
    assert t._fired_entries_today(sch.load_schedule(str(tmp_path))["entries"]) == set(fired)
    # 队列跑完再 tick 即跳过（与真实时钟无关——消费凭标记）
    r = t.tick(["600519"], sched_root=str(tmp_path))
    assert r.get("skipped") == "not-in-schedule"
    # analyze 没下单（持仓仍空），plan 不碰账本 tested by mode
    assert b.broker.get_position("600519") is None


def test_tick_skips_before_first_slot(monkeypatch, tmp_path):
    """时刻未到：tick 直接跳过，不碰 LLM/同步。"""
    from paper_trading.agent import AgentTrader
    from paper_trading.llm import provider as prov

    _mock_net(monkeypatch)
    monkeypatch.setattr(sch, "_now", lambda: datetime(2026, 9, 29, 9, 0))
    monkeypatch.setattr(prov, "chat", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("时段外不应问 LLM")))
    b = _bridge(str(tmp_path), _fresh())
    r = AgentTrader(b).tick(["600519"], sched_root=str(tmp_path))
    assert r.get("skipped") == "not-in-schedule"


def test_legacy_run_means_trade(monkeypatch, tmp_path):
    """直接 run() = 跑一次 trade：时刻未到跳过，到点跑并标记，其次再跑已决策。"""
    import json

    from paper_trading.agent import AgentTrader
    from paper_trading.llm import provider as prov

    _mock_net(monkeypatch)
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(
        {"actions": [], "summary": "不动"}))
    b = _bridge(str(tmp_path), _fresh())
    t = AgentTrader(b)
    monkeypatch.setattr(sch, "_now", lambda: datetime(2026, 9, 29, 9, 0))
    assert t.run(["600519"], sched_root=str(tmp_path))["skipped"] == "not-in-schedule"
    monkeypatch.setattr(sch, "_now", lambda: datetime(2026, 9, 29, 17, 0))
    assert t.run(["600519"], sched_root=str(tmp_path))["ok"]
    assert t._fired_entries_today([{"time": "16:45", "action": "trade"}]) == {"16:45"}
    assert t.run(["600519"], sched_root=str(tmp_path))["skipped"] == "already-decided"


def test_cli_schedule_list_set(tmp_path, monkeypatch):
    """CLI 定时管理：list 读默认/set 落盘/非法输入拒绝（cwd 隔离防污染仓库）。"""
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    import os as _os
    env = dict(_os.environ, PYTHONPATH=str(ROOT))
    base = [sys.executable, "-m", "paper_trading.cli", "agent", "schedule"]

    def _run(*a):
        r = subprocess.run(base + list(a), capture_output=True, text=True,
                           cwd=str(tmp_path), env=env)
        return r, json.loads(r.stdout[r.stdout.index("{"):])

    r, d = _run("list", "--json")
    assert r.returncode == 0, r.stderr
    assert d["data"]["entries"] == [{"time": "16:45", "action": "trade"}]
    r, _ = _run("set", "--entry", "09:00=sync", "--entry", "16:45=trade")
    assert r.returncode == 0, r.stderr
    r, d = _run("list", "--json")
    assert [e["time"] for e in d["data"]["entries"]] == ["09:00", "16:45"]
    r = subprocess.run(base + ["set", "--entry", "99:99=trade"], capture_output=True,
                       text=True, cwd=str(tmp_path), env=env)
    assert r.returncode != 0
    r = subprocess.run(base + ["set", "--entry", "16:45=bomb"], capture_output=True,
                       text=True, cwd=str(tmp_path), env=env)
    assert r.returncode != 0


def test_fired_survives_noisy_log(monkeypatch, tmp_path):
    """回归：面板高频问答把决策挤出最近 N 条窗口时，定时闸不能失忆重跑。"""
    import json

    from paper_trading.agent import AgentTrader
    from paper_trading.llm import provider as prov

    _mock_net(monkeypatch)
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(
        {"actions": [], "summary": "不动"}))
    assert sch.save_schedule(tmp_path, ["16:45=trade"])[0]
    b = _bridge(str(tmp_path), _fresh())
    t = AgentTrader(b)
    monkeypatch.setattr(sch, "_now", lambda: datetime(2026, 9, 29, 17, 0))
    assert t.run(["600519"], sched_root=str(tmp_path))["ok"]
    for i in range(300):  # 300 轮问答噪音，埋掉决策记录
        b.broker.log_operation("llm:ask", {"prompt": f"q{i}"}, True,
                               {"answer": "a"}, None, None)
    assert t._fired_entries_today([{"time": "16:45", "action": "trade"}]) == {"16:45"}
    assert t.run(["600519"], sched_root=str(tmp_path))["skipped"] == "already-decided"
