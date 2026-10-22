"""旧 AI 定时队列兼容单测（生产路径已由运行时接管）。"""
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
    assert sch.parse_entry({"time": "13:00", "action": "PLAN"}) == {
        "time": "13:00", "action": "plan"}
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
    assert sch.load_schedule(tmp_path)["entries"] == [
        {"time": "16:45", "action": "trade"}]


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


def test_cli_schedule_list_set(tmp_path, monkeypatch):
    """旧队列仍可查看/写入（兼容），但生产 tick/run 不再读取它。"""
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
    r = subprocess.run(base + ["set", "--entry", "99:99=trade"],
                       capture_output=True, text=True, cwd=str(tmp_path), env=env)
    assert r.returncode != 0
    r = subprocess.run(base + ["set", "--entry", "16:45=bomb"],
                       capture_output=True, text=True, cwd=str(tmp_path), env=env)
    assert r.returncode != 0
