"""ABC 方案内化能力：论文落库 + prompt 吸收 + 双源误差标记 单测。

覆盖：
- A1: ANALYSIS_PROMPT 含反面检验/偏见自问（规则12）与 thesis 结构（规则13）
- A3a: 复盘 prompt 含三问（空仓买入/停牌5年/论文完整）
- A2: WatchlistThesisDAO upsert/get/get_many/apply_updates；API GET/POST thesis；
      /full 含 thesis；复盘 prompt 注入论文段 + thesis_updates schema
- A3b: _data_quality_facts 提取 _valuation_check；_data_quality_text 渲染双源误差标记
"""

import json
import pytest


# ── A1/A3a/A2 prompt 吸收 ──────────────────────────────────

class TestPromptAbsorb:
    def test_analysis_prompt_has_reverse_check(self):
        from src.analyzer.ai_analyzer import ANALYSIS_PROMPT
        assert "反面检验" in ANALYSIS_PROMPT
        assert "聪明人为什么不买" in ANALYSIS_PROMPT

    def test_analysis_prompt_has_thesis_schema(self):
        from src.analyzer.ai_analyzer import ANALYSIS_PROMPT
        assert '"thesis"' in ANALYSIS_PROMPT
        assert "assumptions" in ANALYSIS_PROMPT
        assert "sell_conditions" in ANALYSIS_PROMPT

    def test_review_prompt_has_three_questions(self, tmp_path, monkeypatch):
        from src.models import database as db_mod
        monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
        db_mod.init_database()
        from src.analyzer.watchlist_reviewer import WatchlistReviewer
        reviewer = WatchlistReviewer({})
        current = [{"code": "000792", "name": "盐湖股份"}]
        prompt = reviewer._build_prompt(
            current, [], {}, [], [])
        assert "今天空仓" in prompt
        assert "停牌 5 年" in prompt
        assert "买入论点还完整吗" in prompt

    def test_review_prompt_injects_thesis_and_schema(self, tmp_path, monkeypatch):
        from src.models import database as db_mod
        monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
        db_mod.init_database()
        db_mod.WatchlistThesisDAO.upsert(
            "000792", "钾肥龙头，成本优势+盐湖资源壁垒",
            [{"content": "钾肥价格维持高位", "verify_method": "季报毛利率",
              "verify_freq": "季度", "status": "未验证"}],
            [{"condition": "毛利率连续两季<50%", "action": "重新评估"}],
            ["PE>40 且透支三年增长"])
        from src.analyzer.watchlist_reviewer import WatchlistReviewer
        reviewer = WatchlistReviewer({})
        prompt = reviewer._build_prompt(
            [{"code": "000792", "name": "盐湖股份"}], [], {}, [], [])
        assert "当前论文与假设" in prompt
        assert "钾肥价格维持高位" in prompt
        assert "thesis_updates" in prompt

    def test_review_prompt_no_thesis_note(self, tmp_path, monkeypatch):
        from src.models import database as db_mod
        monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
        db_mod.init_database()
        from src.analyzer.watchlist_reviewer import WatchlistReviewer
        reviewer = WatchlistReviewer({})
        prompt = reviewer._build_prompt(
            [{"code": "000792", "name": "盐湖股份"}], [], {}, [], [])
        assert "池内暂无论文记录" in prompt


# ── A2 DAO ─────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
    db_mod.init_database()
    return db_mod


class TestThesisDAO:
    def test_upsert_and_get(self, db):
        t = db.WatchlistThesisDAO.upsert(
            "600519", "茅台：品牌壁垒+定价权",
            [{"content": "高端白酒需求稳定", "verify_method": "季报营收",
              "verify_freq": "季度", "status": "未验证"}],
            [{"condition": "营收连续两季负增长", "action": "重新评估"}],
            ["PE>60"])
        assert t["code"] == "600519"
        assert t["assumptions"][0]["status"] == "未验证"
        got = db.WatchlistThesisDAO.get("600519")
        assert got["core_thesis"] == "茅台：品牌壁垒+定价权"
        assert got["sell_conditions"] == ["PE>60"]

    def test_upsert_overwrites_single_row(self, db):
        db.WatchlistThesisDAO.upsert("600519", "旧论点", [], [], [])
        db.WatchlistThesisDAO.upsert("600519", "新论点", [], [], [])
        assert db.WatchlistThesisDAO.get("600519")["core_thesis"] == "新论点"
        with db.db_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM watchlist_thesis WHERE code='600519'"
            ).fetchone()[0]
        assert n == 1

    def test_get_missing_returns_none(self, db):
        assert db.WatchlistThesisDAO.get("999999") is None

    def test_get_many(self, db):
        db.WatchlistThesisDAO.upsert("600519", "论点A", [], [], [])
        db.WatchlistThesisDAO.upsert("000792", "论点B", [], [], [])
        m = db.WatchlistThesisDAO.get_many(["600519", "000792", "300750"])
        assert set(m) == {"600519", "000792"}
        assert db.WatchlistThesisDAO.get_many([]) == {}

    def test_apply_updates_status(self, db):
        db.WatchlistThesisDAO.upsert(
            "600519", "论点", [{"content": "假设1", "status": "未验证"}],
            [{"condition": "c", "action": "a"}], [])
        updated = db.WatchlistThesisDAO.apply_updates(
            "600519",
            [{"content": "假设1", "status": "证伪", "verify_method": "季报"}],
            [{"condition": "c", "action": "a", "triggered": True}])
        assert updated["assumptions"][0]["status"] == "证伪"
        assert updated["red_lines"][0]["triggered"] is True

    def test_apply_updates_no_thesis_returns_none(self, db):
        assert db.WatchlistThesisDAO.apply_updates("999999", [], []) is None


# ── A2 API ─────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "test.db"))
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestThesisAPI:
    def test_get_missing_thesis(self, client):
        resp = client.get("/api/watchlist/600519/thesis")
        assert resp.status_code == 200
        assert resp.json()["thesis"] is None

    def test_post_and_get_thesis(self, client):
        resp = client.post("/api/watchlist/600519/thesis", json={
            "core_thesis": "成本优势+品牌壁垒",
            "assumptions": [{"content": "毛利率≥60%", "status": "未验证"}],
            "red_lines": [{"condition": "毛利率<55%", "action": "重新评估"}],
            "sell_conditions": ["PE>50"],
            "source": "manual",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        got = client.get("/api/watchlist/600519/thesis").json()
        assert got["thesis"]["core_thesis"] == "成本优势+品牌壁垒"
        assert got["thesis"]["assumptions"][0]["status"] == "未验证"

    def test_post_invalid_params(self, client):
        assert client.post("/api/watchlist/600519/thesis",
                           json={"core_thesis": ""}).status_code == 400
        assert client.post("/api/watchlist/600519/thesis", json={
            "core_thesis": "x", "assumptions": "not-list"}).status_code == 400
        assert client.post("/api/watchlist/600519/thesis", json={
            "core_thesis": "x", "source": "hack"}).status_code == 400

    def test_full_includes_thesis(self, client):
        from src.models.database import db_conn
        from src.utils import now_cn
        with db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO stock_snapshot "
                "(code, name, current_price, pe, pb, roe, market_cap, snapshot_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("600519", "贵州茅台", 1500.0, 28.5, 9.2, 25.3, 2000.0,
                 now_cn().strftime("%Y-%m-%d")))
        client.post("/api/watchlist/600519")
        client.post("/api/watchlist/600519/thesis", json={
            "core_thesis": "测试论点", "source": "manual"})
        data = client.get("/api/watchlist/600519/full").json()
        assert data["thesis"]["core_thesis"] == "测试论点"


# ── A3b 双源误差标记 ───────────────────────────────────────

class TestValuationCheck:
    def test_facts_extract_check(self):
        from src.analyzer.ai_analyzer import _data_quality_facts
        facts = _data_quality_facts({
            "code": "600519",
            "_valuation_check": {"market_cap": {"verdict": "FAIL"},
                                 "pe": {"verdict": "PASS"}},
        })
        assert facts["valuation_check"]["market_cap"]["verdict"] == "FAIL"

    def test_text_renders_fail_marker(self):
        from src.analyzer.ai_analyzer import _data_quality_text
        facts = {"valuation_check": {
            "market_cap": {"verdict": "FAIL", "calculated_yi": 3800.0,
                           "reported": 2000.0, "deviation_pct": 90.0},
            "pe": {"verdict": "SKIP"},
        }}
        text = _data_quality_text(facts)
        assert "双源误差标记" in text
        assert "FAIL" in text
        assert "置信度降级" in text

    def test_text_no_marker_when_pass(self):
        from src.analyzer.ai_analyzer import _data_quality_text
        facts = {"valuation_check": {
            "market_cap": {"verdict": "PASS"}, "pe": {"verdict": "PASS"}}}
        text = _data_quality_text(facts)
        assert "双源误差标记" in text  # 通过时也出一行（验算通过）
        assert "置信度降级" not in text

    def test_text_no_check_key(self):
        """无验算数据（_valuation_check 缺失）不渲染双源行，不抛异常。"""
        from src.analyzer.ai_analyzer import _data_quality_text
        text = _data_quality_text({})
        assert "双源误差标记" not in text

    def test_text_all_skip_says_not_verified(self):
        """P0-2：双源全 SKIP 必须如实报“未执行”，不许谎报通过；原因透传。"""
        from src.analyzer.ai_analyzer import _data_quality_text
        facts = {"valuation_check": {
            "market_cap": {"verdict": "SKIP", "note": "缺现价/总股本/快照市值"},
            "pe": {"verdict": "SKIP", "note": "缺现价/eps 或 eps 非正"}}}
        text = _data_quality_text(facts)
        assert "双源验算未执行" in text
        assert "缺现价/总股本/快照市值" in text
        assert "这不是通过" in text
        assert "验算通过" not in text

    def test_text_partial_skip_pass_shows_skip_reason(self):
        """一项通过 + 一项跳过：如实报通过同时透传跳过原因。"""
        from src.analyzer.ai_analyzer import _data_quality_text
        facts = {"valuation_check": {
            "market_cap": {"verdict": "PASS"},
            "pe": {"verdict": "SKIP", "note": "缺现价/eps 或 eps 非正"}}}
        text = _data_quality_text(facts)
        assert "验算通过" in text
        assert "缺现价/eps" in text
