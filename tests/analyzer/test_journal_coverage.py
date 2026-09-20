"""journal 全覆盖校验单测（B7 周报深度版发布前校验）

直测生产函数 check_journal_coverage + _validate_and_persist 落库集成：
缺股记账、全覆盖不记账，且记账永不阻断落库（warn-only）。
"""

import json

from src.analyzer.watchlist_reviewer import (
    WatchlistReviewer,
    check_journal_coverage,
)


def test_check_journal_coverage_flags_missing():
    """缺一只记一只"""
    missing = check_journal_coverage(
        {"000792", "600519"},
        "# 本周复盘\n## 池变动\n调入 000792 盐湖股份\n## 逐股分析\n000792: ...",
    )
    assert missing == ["600519"]


def test_check_journal_coverage_full_and_empty():
    """全覆盖不记账；正文为空全记账；空池不记账"""
    assert check_journal_coverage(
        {"000792", "600519"}, "000792 ... 600519 ...") == []
    assert check_journal_coverage(
        {"000792", "600519"}, "") == ["000792", "600519"]
    assert check_journal_coverage(set(), "# 复盘") == []


def test_validate_persist_records_coverage_missing_warn_only(
        monkeypatch, tmp_path):
    """集成：journal 缺股 → actions_summary.coverage_missing 记账，落库照常"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.models.ai_watchlist import AiWatchlistDAO, AiJournalDAO
    AiWatchlistDAO().add("600519", "贵州茅台", "测试")
    AiWatchlistDAO().add("000792", "盐湖股份", "测试")

    reviewer = WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "watchlist_size": 5,
                      "hard_rules": {}},
        "ai": {"api_base": "https://example.com", "model": "test-free"},
    })
    result = {
        "watchlist_actions": [
            {"code": "600519", "action": "keep", "reason": "基本面稳定"},
            {"code": "000792", "action": "keep", "reason": "基本面稳定"},
        ],
        "new_watchlist": ["600519", "000792"],
        # 正文只提 600519，漏了 000792
        "journal": {"title": "复盘", "content_md": "# 复盘\n600519 保持"},
    }
    out = reviewer._validate_and_persist(
        result, "run_cov_01", [], AiWatchlistDAO().get_all(), [],
        analyses={})

    # warn-only：落库未被阻断
    assert out["journal"] == result["journal"]
    saved = AiJournalDAO().get_latest()
    assert saved is not None
    assert saved["title"] == "复盘"
    summ = json.loads(saved["actions_summary"])
    assert summ["coverage_missing"] == ["000792"]
    assert summ["keep"] == 2
    assert len(AiWatchlistDAO().get_all()) == 2
