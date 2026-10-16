"""两项回归门禁（2026-10-16 线上故障驱动）：

1) K线按需构建：库内无该股日K（用户搜索的新股/冷门股）时自动拉取落库，不空图。
2) 矛盾检测：journal 页面脚本结构（loadConflicts 必须在顶层，曾被吞进
   showCompareDialog 导致永远停在“检测中…”）+ 后端 actions_summary 缺失时
   漂移检测仍要跑（曾 NameError 被裸 except 吞掉）。

hermetic：一律走 tmp 库（禁网、禁 ambient）。
"""
import json
import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """只要库不要 HTTP：给直接调 DAO/纯函数的用例用。"""
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
    db_mod.init_database()
    return db_mod


# ── 1. K线按需构建 ──────────────────────────────────────────────

def _kline_rows(n=5):
    return [{"trade_date": f"2026-01-{i + 1:02d}", "open": 10.0, "close": 11.0,
             "high": 12.0, "low": 9.0, "volume": 100.0, "amount": 1000.0,
             "turnover": 1.0} for i in range(n)]


def test_kline_builds_when_db_empty(client, monkeypatch):
    """库里没有 → 拉数据源 + 落库 + 返回（原来直接空数组）"""
    calls = []

    def fake_fetch(code, *a, **k):
        calls.append(code)
        return _kline_rows()

    monkeypatch.setattr("src.collector.akshare_fetcher.fetch_kline_data", fake_fetch)
    resp = client.get("/api/stock/300750/kline")
    assert resp.status_code == 200
    assert calls == ["300750"], "空库必须触发按需拉取"
    assert len(resp.json()["klines"]) == 5
    from src.models.database import KlineDAO
    assert len(KlineDAO().get_daily("300750")) == 5, "必须落库，供后续请求复用"


def test_kline_uses_db_without_fetch(client, monkeypatch):
    """库里有 → 不碰数据源（避免每次刷页都打外网）"""
    def boom(code, *a, **k):
        raise AssertionError("库内已有数据时不应调用数据源")

    monkeypatch.setattr("src.collector.akshare_fetcher.fetch_kline_data", boom)
    from src.models.database import KlineDAO
    KlineDAO().upsert_many("600519", _kline_rows(3))
    resp = client.get("/api/stock/600519/kline")
    assert resp.status_code == 200
    assert len(resp.json()["klines"]) == 3


def test_kline_fetch_failure_returns_empty_not_500(client, monkeypatch):
    """数据源炸了也不能 5xx——详情页要照常开，只是图空着"""
    def boom(code, *a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("src.collector.akshare_fetcher.fetch_kline_data", boom)
    resp = client.get("/api/stock/002230/kline")
    assert resp.status_code == 200
    assert resp.json()["klines"] == []


def test_kline_invalid_code_still_404(client):
    assert client.get("/api/stock/abc/kline").status_code == 404


# ── 2. 矛盾检测（页面结构）──────────────────────────────────────

def _inline_script(rel: str) -> str:
    from pathlib import Path
    html = open(str(Path(rel).resolve()), encoding="utf-8").read()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts, "模板应有内联脚本"
    return scripts[0]


def _depth_at(src: str, pos: int) -> int:
    """pos 之前的括号深度（跳过字符串/模板串），判断代码是否在顶层。"""
    depth, state, esc = 0, None, False
    for ch in src[:pos]:
        if state:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == state:
                state = None
        else:
            if ch in "'\"`":
                state = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
    return depth


def test_journal_conflict_loader_is_top_level():
    """回归：整段脚本曾被吞进 showCompareDialog()，loadConflicts 变死代码，
    页面永远停在“检测中…”。断言它在顶层（深度 0）。"""
    src = _inline_script("src/web/templates/journal.html")
    for key in ("function loadConflicts", "function startConflictCheck"):
        pos = src.find(key)
        assert pos != -1, f"模板缺少 {key}"
        assert _depth_at(src, pos) == 0, f"{key} 被嵌在别的函数里，矛盾检测不会执行"


def test_journal_conflict_check_not_only_on_domcontentloaded():
    """DOMContentLoaded 在该页偶发不触发，必须直接调一次。"""
    src = _inline_script("src/web/templates/journal.html")
    assert "startConflictCheck();" in src


# ── 3. 矛盾检测（后端）─────────────────────────────────────────

def test_load_summary_never_raises():
    """actions_summary 为空/坏 JSON 时返回 {}，不让漂移检测崩掉"""
    from src.web.routes import _load_summary
    assert _load_summary(None) == {}
    assert _load_summary("") == {}
    assert _load_summary("not json") == {}
    assert _load_summary("[1,2]") == {}
    assert _load_summary('{"model": "m"}')["model"] == "m"


def test_conflicts_endpoint_survives_missing_summary(client):
    """回归：任一期 actions_summary 为空时，漂移检测曾因 NameError 被裸 except
    吞掉（等于从未运行）。接口必须照常给出结构化结果。"""
    from src.models.ai_watchlist import AiJournalDAO

    dao = AiJournalDAO()
    dao.save("2026-09-01", "r1", "第一期", "# 一\n正文", "{}",
             json.dumps({"model": "m1",
                         "details": {"add": [{"code": "300390"}]}}))
    dao.save("2026-09-08", "r2", "第二期", "# 二\n正文", "{}", None)

    resp = client.get("/api/journal/2026-09-08/conflicts")
    assert resp.status_code == 200
    body = resp.json()
    assert "conflicts" in body and body["previous_date"] == "2026-09-01"


def test_detect_history_signal_flip_between_two_dates(tmp_db):
    """两期笔记之间的 Signal 翻转要能查出来（论点被打脸）"""
    from src.web.routes import _detect_history_signal_flips

    with tmp_db.db_conn() as conn:
        for day, sig in (("2026-09-07", "BUY"), ("2026-09-09", "AVOID")):
            conn.execute(
                "INSERT INTO stock_analysis_history (stock_code, run_id,"
                " analysis_date, score, ai_analysis, ai_trade_strategy, model,"
                " created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("300390", "r-" + day, day, 90.0, "{}",
                 json.dumps({"signal": sig}), "m", day))

    flips = _detect_history_signal_flips({"300390"}, "2026-09-08", "2026-09-10")
    assert len(flips) == 1, flips
    assert flips[0]["type"] == "signal_flip_history"
    assert "BUY" in flips[0]["message"] and "AVOID" in flips[0]["message"]


def test_detect_history_signal_flip_needs_both_dates(tmp_db):
    from src.web.routes import _detect_history_signal_flips
    assert _detect_history_signal_flips({"300390"}) == []
    assert _detect_history_signal_flips({"300390"}, "2026-09-08", "") == []
