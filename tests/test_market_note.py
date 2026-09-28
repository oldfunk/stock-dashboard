"""大盘解盘写笔记单测（不限 token + 同日复盘行追加语义）。"""

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
    db_mod.init_database()
    return db_mod


def _seed(db_mod):
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT INTO market_index (index_code, index_name, current_value,"
            " change_percent, timestamp, date) VALUES (?,?,?,?,?,?)",
            ("sh000001", "上证指数", 3000.0, 0.5,
             "2026-09-28T15:00:00", "2026-09-28"))
        conn.execute(
            "INSERT INTO stock_snapshot (code, name, pe, current_price,"
            " snapshot_date) VALUES (?,?,?,?,?)",
            ("600519", "贵州茅台", 20.0, 1500.0, "2026-09-28"))
        conn.execute(
            "INSERT INTO screening_result (run_id, run_date, code, name,"
            " score) VALUES (?,?,?,?,?)",
            ("r1", "2026-09-28", "600519", "贵州茅台", 90.0))
    db_mod.RunLogDAO().start_run("r1")
    db_mod.RunLogDAO().complete_run("r1", 5527, 1, 0)


def _fake_ask(text="一、结论\n很长很长的分析"):
    def _fn(self, prompt, system=None, timeout=120.0, unlimited=False):
        _fn.seen = {"prompt": prompt, "system": system,
                    "timeout": timeout, "unlimited": unlimited}
        return (text, "m",
                {"prompt_tokens": 100, "completion_tokens": 9000})
    return _fn


class TestWriteMarketNote:
    def test_writes_journal_unlimited(self, db, monkeypatch):
        from src import ai_queue
        from src.analyzer import ai_analyzer as mod
        from src.models.ai_watchlist import AiJournalDAO
        fake = _fake_ask()
        monkeypatch.setattr(mod.AiAnalyzer, "ask_raw", fake)
        monkeypatch.setattr(mod.AiAnalyzer, "configured",
                            property(lambda self: True))
        _seed(db)
        out = ai_queue.write_market_note("重点看白酒", today="2026-09-28")
        assert out["journal_date"] == "2026-09-28"
        assert out["chars"] > 0
        assert fake.seen["unlimited"] is True
        assert fake.seen["timeout"] == 600.0
        assert "贵州茅台" in fake.seen["prompt"]
        assert "重点看白酒" in fake.seen["prompt"]
        assert "上证指数" in fake.seen["prompt"]
        row = AiJournalDAO().get_by_date("2026-09-28")
        assert row is not None
        assert "大盘解盘" in row["title"]
        assert "一、结论" in row["content_md"]

    def test_appends_to_review_row(self, db, monkeypatch):
        from src import ai_queue
        from src.analyzer import ai_analyzer as mod
        from src.models.ai_watchlist import AiJournalDAO
        import json as _json
        monkeypatch.setattr(mod.AiAnalyzer, "ask_raw", _fake_ask("new"))
        monkeypatch.setattr(mod.AiAnalyzer, "configured",
                            property(lambda self: True))
        _seed(db)
        dao = AiJournalDAO()
        dao.save("2026-09-28", "r1", "周六复盘", "复盘正文",
                 None, _json.dumps({"add": 1, "remove": 0, "keep": 4}))
        out = ai_queue.write_market_note(today="2026-09-28")
        assert out is not None
        row = dao.get_by_date("2026-09-28")
        assert "复盘正文" in row["content_md"]
        assert "new" in row["content_md"]

    def test_unconfigured(self, monkeypatch):
        from src import ai_queue
        from src.analyzer import ai_analyzer as mod
        monkeypatch.setattr(mod.AiAnalyzer, "configured",
                            property(lambda self: False))
        assert ai_queue.write_market_note() is None
