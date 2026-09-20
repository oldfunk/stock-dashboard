"""watch 状态机生命周期单测（B6a 状态机 + 周六 live 验证补强）

覆盖 core/watch/dropped 三态流转，均走 review() dry-run（mock _call_llm），
不断言 prompt 文本，只断言落库状态 + journal 记账。
"""

import json

from src.analyzer.watchlist_reviewer import WatchlistReviewer


def _seed_screening(recent):
    from src.models.database import ScreeningResultDAO
    ScreeningResultDAO().save_batch([
        {"run_id": "r1", "run_date": recent, "code": "600519",
         "name": "贵州茅台", "score": 85, "pe": 30, "pb": 10,
         "roe": 30, "gross_margin": 90, "net_margin": 50,
         "ocf_per_share": 50, "revenue_growth": 15, "profit_growth": 20,
         "debt_ratio": 20, "market_cap": 2000, "reason": "测试"},
        {"run_id": "r1", "run_date": recent, "code": "002415",
         "name": "海康威视", "score": 70, "pe": 20, "pb": 5,
         "roe": 15, "gross_margin": 40, "net_margin": 20,
         "ocf_per_share": 2, "revenue_growth": 5, "profit_growth": 3,
         "debt_ratio": 40, "market_cap": 500, "reason": "测试"},
    ])


def _fresh_db(monkeypatch, tmp_path):
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.utils import now_cn
    from datetime import timedelta
    recent = (now_cn() - timedelta(days=3)).strftime("%Y-%m-%d")
    _seed_screening(recent)


def _reviewer():
    return WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "watchlist_size": 5,
                      "hard_rules": {"signal_avoid": True, "roe_below": 5,
                                     "price_above_buyzone_pct": 20,
                                     "roe_collapse_pp": 10,
                                     "veto_triggered": True}},
        "ai": {"api_base": "https://example.com", "model": "test-free"},
    })


def test_core_to_watch_persists_observe_items(monkeypatch, tmp_path):
    """core→watch：基本面恶化未达硬规则线，LLM 判 watch → 留池 + 观察项/期限落库"""
    _fresh_db(monkeypatch, tmp_path)
    from src.models.ai_watchlist import AiWatchlistDAO, AiJournalDAO
    AiWatchlistDAO().add("600519", "贵州茅台", "ROE 30%+")

    reviewer = _reviewer()

    def fake_call_llm(prompt):
        return {
            "watchlist_actions": [
                {"code": "600519", "action": "watch",
                 "reason": "毛利率拐点待确认", "watch_until": "2026-10-15"}
            ],
            "new_watchlist": ["600519"],
            "journal": {"title": "复盘",
                        "content_md": "# 复盘\n600519 毛利率拐点待确认，继续观察"},
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    reviewer.review("run_lc_watch_01")

    row = AiWatchlistDAO().get_by_code("600519")
    assert row["status"] == "watch"
    assert row["status_reason"] == "毛利率拐点待确认"
    assert row["watch_until"] == "2026-10-15"
    assert len(AiWatchlistDAO().get_all()) == 1  # watch 仍在池
    summ = json.loads(AiJournalDAO().get_latest()["actions_summary"])
    assert summ["watch"] == 1
    assert summ["coverage_missing"] == []  # 正文提到代码 → 不记账


def test_hard_rules_override_watch_two_rules(monkeypatch, tmp_path):
    """2 条硬规则触发时 LLM 的 watch 被强制改写为 remove → dropped

    002415 同时触发 signal_avoid + veto_triggered（ai_watchlist 表无
    roe/current_price 列，真实 review 路径只有分析驱动的两条规则可触发）。
    """
    _fresh_db(monkeypatch, tmp_path)
    from src.models.ai_watchlist import AiWatchlistDAO, AiJournalDAO
    from src.models.database import StockAnalysisHistoryDAO
    AiWatchlistDAO().add("002415", "海康威视", "测试")
    StockAnalysisHistoryDAO().save(
        "002415", "r1", 70.0,
        json.dumps({"analysis": "测试",
                    "veto_checklist": {"triggered_count": 1,
                                       "management_integrity_issue": True}}),
        json.dumps({"signal": "AVOID"}),
    )

    reviewer = _reviewer()

    def fake_call_llm(prompt):
        return {
            "watchlist_actions": [
                {"code": "002415", "action": "watch",
                 "reason": "再观察一周", "watch_until": "2026-10-15"}
            ],
            "new_watchlist": ["002415"],
            "journal": {"title": "复盘", "content_md": "# 复盘\n002415 观察中"},
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    reviewer.review("run_lc_watch_02")

    assert "002415" not in {w["code"] for w in AiWatchlistDAO().get_all()}
    dropped = AiWatchlistDAO().get_by_code("002415")
    assert dropped["status"] == "dropped"
    assert "硬规则强制调出" in dropped["status_reason"]
    assert "signal_avoid" in dropped["status_reason"]
    assert "veto_triggered" in dropped["status_reason"]
    assert "002415" in {w["code"]
                        for w in AiWatchlistDAO().get_all(include_dropped=True)}
    summ = json.loads(AiJournalDAO().get_latest()["actions_summary"])
    assert summ["remove"] == 1
    assert summ["watch"] == 0


def test_watch_to_dropped_after_two_cycles(monkeypatch, tmp_path):
    """watch→dropped：第一周期 watch，第二周期（期限过后）LLM 判 remove → 软删除"""
    _fresh_db(monkeypatch, tmp_path)
    from src.models.ai_watchlist import (
        AiWatchlistDAO, AiWatchlistHistoryDAO, AiJournalDAO,
    )
    AiWatchlistDAO().add("600519", "贵州茅台", "ROE 30%+")

    reviewer = _reviewer()

    def fake_watch(prompt):
        return {
            "watchlist_actions": [
                {"code": "600519", "action": "watch",
                 "reason": "等财报确认", "watch_until": "2026-09-01"}
            ],
            "new_watchlist": ["600519"],
            "journal": {"title": "复盘一", "content_md": "# 复盘一\n600519 等财报"},
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_watch)
    reviewer.review("run_lc_watch_03a")
    assert AiWatchlistDAO().get_by_code("600519")["status"] == "watch"

    def fake_remove(prompt):
        return {
            "watchlist_actions": [
                {"code": "600519", "action": "remove", "reason": "观察期满未改善"}
            ],
            "new_watchlist": [],
            "journal": {"title": "复盘二", "content_md": "# 复盘二\n无变动及原因"},
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_remove)
    reviewer.review("run_lc_watch_03b")

    assert "600519" not in {w["code"] for w in AiWatchlistDAO().get_all()}
    dropped = AiWatchlistDAO().get_by_code("600519")
    assert dropped["status"] == "dropped"
    assert dropped["status_reason"] == "观察期满未改善"
    hist = AiWatchlistHistoryDAO().list_by_code("600519")
    seq = [h["action"] for h in sorted(hist, key=lambda r: r["id"])]
    assert seq == ["watch", "remove"]  # 两周期动作都在账上
    summ = json.loads(AiJournalDAO().get_latest()["actions_summary"])
    assert summ["remove"] == 1


def test_watch_to_core_on_keep(monkeypatch, tmp_path):
    """watch→core 回流：观察项证伪/无事，LLM 判 keep → 状态回到 core"""
    _fresh_db(monkeypatch, tmp_path)
    from src.models.ai_watchlist import AiWatchlistDAO
    AiWatchlistDAO().add("600519", "贵州茅台", "ROE 30%+")
    AiWatchlistDAO().set_status("600519", "watch", "等财报确认", "2026-10-15")

    reviewer = _reviewer()

    def fake_keep(prompt):
        return {
            "watchlist_actions": [
                {"code": "600519", "action": "keep", "reason": "财报确认无虞"}
            ],
            "new_watchlist": ["600519"],
            "journal": {"title": "复盘", "content_md": "# 复盘\n600519 财报确认无虞"},
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_keep)
    reviewer.review("run_lc_watch_04")

    row = AiWatchlistDAO().get_by_code("600519")
    assert row["status"] == "core"
    assert row["watch_until"] is None
    assert row["review_count"] == 2
    assert len(AiWatchlistDAO().get_all()) == 1
