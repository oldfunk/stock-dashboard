"""T22 心跳与看门狗：故障要响、恢复要自动消、非应跳时段不许误报、心跳件绝不能拖垮交易链。

背景：Pi 连续自重启无人发现（`docs/migration-nec.md`），靠用户肉眼看出。
时间用显式常量注入（2026-10-28 是周三=交易日），不依赖真实时钟 ⇒ 测试可重复。
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.utils import heartbeat as hb        # noqa: E402

MORNING = datetime(2026, 10, 28, 10, 0)     # 交易日交易时段：engine 应跳
LATE = datetime(2026, 10, 28, 21, 0)       # 交易日 21:00：agent 应跳、engine 不应跳
NIGHT = datetime(2026, 10, 28, 21, 30)     # engine 不应跳（15:59 之后）


class FakeRun:
    """假 subprocess.run：可脚本化返回，或整体抛错（cron 里没 systemctl / 无 user 会话）。"""

    def __init__(self, mapping=None, raises=False):
        self.mapping = mapping or {}
        self.raises = raises

    def __call__(self, cmd, **kw):
        if self.raises:
            raise OSError("systemctl not found")

        class R:
            stdout = "inactive\n"

        r = R()
        key = " ".join(cmd)
        for pat, out in self.mapping.items():
            if pat in key:
                r.stdout = out
                return r
        return r


def _seed(tmp, name, ts, ok=True, detail=""):
    """直接写一条心跳记录（用于制造"陈旧"状态）。"""
    p = tmp / "heartbeat.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data[name] = {"ts": ts.isoformat(timespec="seconds"), "ok": ok, "detail": detail}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_fresh_beat_is_healthy(tmp_path):
    p = tmp_path / "var" / "heartbeat.json"
    assert hb.touch("engine", path=p, now=MORNING) is True
    out = hb.probe({"engine": 300}, path=p, now=MORNING)
    assert out["healthy"] is True, out
    assert out["checks"]["engine"]["state"] == "ok"


def test_stale_during_session_is_unhealthy(tmp_path):
    p = _seed(tmp_path, "engine", MORNING - timedelta(seconds=900))
    out = hb.probe({"engine": 300}, path=p, now=MORNING)
    assert out["healthy"] is False and out["unhealthy"] == ["engine"], out


def test_missing_record_is_unhealthy_fail_closed(tmp_path):
    """应跳时段里"根本没记录"必须算不健康——没有证据就是没跑。"""
    out = hb.probe({"engine": 300}, path=tmp_path / "nope.json", now=MORNING)
    assert out["healthy"] is False and out["checks"]["engine"]["state"] == "stale", out


def test_not_due_window_never_alerts(tmp_path):
    """engine 夜里没跳不算故障（cron 本来就不跑）——否则天天误报，告警失去意义。"""
    p = _seed(tmp_path, "engine", MORNING - timedelta(days=1))
    out = hb.probe({"engine": 300}, path=p, now=NIGHT)
    # 只看 engine 这一项：夜里不该跳 ⇒ not-due，绝不进 unhealthy
    assert out["checks"]["engine"]["state"] == "not-due", out
    assert "engine" not in out["unhealthy"], out
    # 同一时刻 agent 已过收盘计划窗口却没跳 ⇒ 才是真故障
    out2 = hb.probe({"agent": 64800}, path=p, now=LATE)
    assert out2["healthy"] is False and out2["unhealthy"] == ["agent"], out2


def test_corrupt_heartbeat_file_degrades_safely(tmp_path):
    p = tmp_path / "heartbeat.json"
    p.write_text("{not json", encoding="utf-8")
    assert hb.load(p) == {}
    assert hb.age("engine", path=p) is None
    assert hb.probe({"engine": 300}, path=p, now=MORNING)["healthy"] is False


def test_touch_never_raises_on_bad_path(tmp_path):
    """心跳件绝不能把交易轮次拖挂：写不进去只返回 False。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file", encoding="utf-8")
    assert hb.touch("engine", path=blocker / "sub" / "hb.json") is False


def test_dashboard_active_probe():
    assert hb.dashboard_active(FakeRun({"is-active paper-dashboard.service": "active\n"})) == "active"
    assert hb.dashboard_active(FakeRun()) == "inactive"
    assert hb.dashboard_active(FakeRun(raises=True)) == "unknown"


def test_check_writes_status_then_recovery_clears(tmp_path):
    hb_p, st_p = tmp_path / "heartbeat.json", tmp_path / "health_status.json"
    _seed(tmp_path, "engine", MORNING - timedelta(seconds=900))
    st = hb.check({"engine": 300}, path=hb_p, now=MORNING,
                  runner=FakeRun(), status_path=st_p)
    assert st["healthy"] is False and st["alerts"], st
    saved = json.loads(st_p.read_text(encoding="utf-8"))
    assert saved["healthy"] is False
    assert hb.transitioned({}, saved) == "degraded"        # 首次发现故障必须记一条
    assert hb.transitioned(saved, saved) is None           # 持续故障不刷屏
    hb.touch("engine", path=hb_p, now=MORNING)
    st2 = hb.check({"engine": 300}, path=hb_p, now=MORNING,
                   runner=FakeRun({"is-active": "active\n"}), status_path=st_p)
    assert st2["healthy"] is True and st2["alerts"] == [], st2
    assert hb.transitioned(saved, st2) == "recovered"      # 红条自动消除


def test_dashboard_down_alone_alerts(tmp_path):
    hb_p, st_p = tmp_path / "heartbeat.json", tmp_path / "health_status.json"
    hb.touch("engine", path=hb_p, now=MORNING)
    st = hb.check({"engine": 300}, path=hb_p, now=MORNING,
                  runner=FakeRun(), status_path=st_p)
    assert st["healthy"] is False and any("面板" in a for a in st["alerts"]), st


def test_engine_round_beats_even_when_both_crash(tmp_path, monkeypatch):
    """轮次跑完就心跳（哪怕两个模块都崩）；都错 ⇒ ok=False 且错误进 detail。"""
    import paper_trading.engine.runner as rr

    beats = []
    monkeypatch.setattr(
        hb, "touch",
        lambda name, **kw: beats.append((name, kw.get("ok"), kw.get("detail"))) or True)

    class Dead:
        state = None

        def tick(self):
            raise RuntimeError("boom-rules")

    class StubBridge:
        account_db_path = str(tmp_path / "a.db")
        data_db_path = str(tmp_path / "d.db")
        config_path = str(tmp_path / "none.yaml")

    monkeypatch.setattr(rr, "EngineRunner", lambda *a, **k: Dead())
    monkeypatch.setattr(rr, "ConditionRunner", lambda *a, **k: Dead())
    monkeypatch.setattr(rr, "load_engine_config",
                        lambda *a, **k: {"enabled": False, "rules": []})
    rr.run_loop(StubBridge(), interval_sec=1, max_ticks=1)

    assert len(beats) == 1 and beats[0][0] == "engine", beats
    assert beats[0][1] is False, beats
    assert "boom-rules" in (beats[0][2] or ""), beats
