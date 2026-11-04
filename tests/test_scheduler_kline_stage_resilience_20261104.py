"""15-a（2026-11-04）：2026-11-03 产线事故的回归用例。

事故实录：15:30 流水线主阶段跑完，增量阶段撞上 **920156**（北交所首只进观察池/Top25
的代码），腾讯路径抛的 ``SourceUnsupportedError``（批12-a 设计的能力边界）逃逸出
``_fetch_kline_daily`` 的循环 → 整个增量阶段中止 → **其后的 F2 温度计快照整天缺席**。
母方 ``e638083`` 已经修好（逐 code 容错 + 阶段再包一层非致命 try），但那提交没带用例；
能力边界这类"异常不是错误"的语义最容易在下一个调用点再漏一次，所以三层都钉住：
循环不中断、阶段不阻断快照、源头仍按设计抛（不许退化成空列表）。
"""

import datetime as dt

import pytest

from src.collector.akshare_fetcher import SourceUnsupportedError
from src.models import database as db_mod

POOL = ["920156", "600519", "000001", "300750"]


def _rows(day="2026-11-03"):
    return [{"trade_date": day, "open": 10.0, "close": 10.0, "high": 10.5,
             "low": 9.5, "volume": 100.0, "amount": 1000.0,
             "close_raw": 10.0, "close_adj": 20.0}]


@pytest.fixture
def stage(tmp_path, monkeypatch):
    """只带 tmp 库的调度器实例 + 可控的观察池/候选池（返回可打桩的模块句柄）。"""
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "s.db"))
    db_mod.init_database()
    import src.scheduler as s
    import src.models.ai_watchlist as aw
    import src.collector.akshare_fetcher as f
    m = s.MarketScheduler()
    monkeypatch.setattr(aw.AiWatchlistDAO, "get_all",
                        lambda self: [{"code": c} for c in POOL])
    monkeypatch.setattr(db_mod.ScreeningResultDAO, "get_latest_results",
                        lambda self, limit=25: [])
    return m, f


def _written_codes():
    with db_mod.db_conn() as conn:
        return {r["code"] for r in conn.execute(
            "SELECT DISTINCT code FROM kline_daily")}


def test_capability_boundary_skips_only_that_code(stage, monkeypatch):
    """BJ 抛能力边界：其余三只照拉照写，阶段本身不抛（事故本体），且跳过留痕。"""
    m, f = stage
    seen = []

    def fake(code, start_date=None, adjust="qfq"):
        seen.append(code)
        if code == "920156":
            raise SourceUnsupportedError("腾讯 K 线源不支持北交所代码 920156")
        return _rows()

    monkeypatch.setattr(f, "fetch_kline_data", fake)
    # 用 logger 间谍而不是 caplog：本仓 logging_config 自带 handler，
    # 记录不向 root 传播，caplog 抓不到（第一版就是这么踩空的）。
    import src.scheduler as s
    logged = []

    class Spy:
        def info(self, msg, *a):
            logged.append(("info", msg % a if a else msg))

        def warning(self, msg, *a):
            logged.append(("warning", msg % a if a else msg))

        def error(self, msg, *a):
            logged.append(("error", msg % a if a else msg))

    monkeypatch.setattr(s, "logger", Spy())
    m._fetch_kline_daily()                       # 不该抛
    assert set(seen) == set(POOL), "循环必须把每一只都走完"
    assert _written_codes() == {"600519", "000001", "300750"}
    text = " | ".join(t for _lvl, t in logged)
    assert "能力边界" in text and "920156" in text, "跳过要留痕，不能静默"
    assert "跳过 1" in text, "汇总里要能看见跳过了几只"


def test_network_error_also_does_not_abort_the_stage(stage, monkeypatch):
    """限流/网络异常同样只算单只失败：需要"别拖垮整段"的不止能力边界一种形状。"""
    m, f = stage
    seen = []

    def fake(code, start_date=None, adjust="qfq"):
        seen.append(code)
        if code == "600519":
            raise RuntimeError("RemoteDisconnected")
        return _rows()

    monkeypatch.setattr(f, "fetch_kline_data", fake)
    m._fetch_kline_daily()
    assert set(seen) == set(POOL)
    assert _written_codes() == {"920156", "000001", "300750"}


def test_snapshot_still_runs_when_the_kline_stage_explodes(tmp_path, monkeypatch):
    """阶段级第二层：增量整体失败也不能让当日温度计快照缺席（11-03 的失守点）。"""
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "s2.db"))
    db_mod.init_database()
    import src.scheduler as s
    import src.orchestrator as o
    import src.research.market_state as ms

    m = s.MarketScheduler()
    monkeypatch.setattr(s, "now_cn", lambda: dt.datetime(2026, 11, 4, 16, 0))
    m._last_daily_date = None
    monkeypatch.setattr(o, "run_daily_pipeline", lambda config=None: True)

    def boom():
        raise RuntimeError("整段炸了")
    monkeypatch.setattr(m, "_fetch_kline_daily", boom)

    snapped = []

    class Sv:
        status, coverage = "pass", 1.0

    monkeypatch.setattr(ms, "snapshot_day",
                        lambda d=None, **kw: snapped.append(d) or Sv())
    m._check_daily_pipeline()
    assert snapped, "market_state 快照必须仍被调用一次"
    assert m._last_daily_date == dt.date(2026, 11, 4), "主阶段成功就该置完成"


def test_bj_still_escapes_as_capability_boundary_not_empty(monkeypatch):
    """源头契约不许退化：BJ 在腾讯路径必须**抛**，不能返回 []。

    返回 [] 会被上层当成"这只票没数据" → 记 no_data → 被进度表永久排除
    （批10-c 缺陷 B 就是这一个形状）。
    """
    import src.collector.akshare_fetcher as f
    monkeypatch.setattr(f, "_fetch_kline_em", lambda *a, **k: [])   # 东财限流
    with pytest.raises(SourceUnsupportedError):
        f.fetch_kline_data("920156", start_date="2026-01-01")


def test_sh_code_falls_back_to_tx_without_raising(monkeypatch):
    """对照：同一路径对沪深只是回退取数，不抛能力边界（防守卫瞄错市场）。"""
    import src.collector.akshare_fetcher as f
    monkeypatch.setattr(f, "_fetch_kline_em", lambda *a, **k: [])
    called = []

    def fake_tx(code, start_str, end_str, adjust="qfq"):
        called.append(adjust)
        if adjust == "":
            return [{"trade_date": "2026-01-05", "close": 10.0}]
        return []                      # hfq 空 → 合流不成，整体返回 []（不抛）

    monkeypatch.setattr(f, "_fetch_kline_tx", fake_tx)
    assert f.fetch_kline_data("600519", start_date="2026-01-01") == []
    assert called == ["", "hfq"], "腾讯双拉仍按 ''/hfq 两口径都试过"
