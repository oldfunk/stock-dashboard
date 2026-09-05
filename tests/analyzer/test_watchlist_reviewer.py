"""WatchlistReviewer 硬规则层单测

4 条硬规则各自触发条件 + 不触发情况。
"""

import json

import pytest

from src.analyzer.watchlist_reviewer import (
    WatchlistReviewer,
    check_signal_avoid,
    check_roe_below,
    check_price_above_buyzone,
    check_roe_collapse,
    check_veto_triggered,
)


def test_signal_avoid_triggered():
    """规则 1: Signal=AVOID → 强制调出"""
    stock = {"code": "002415", "name": "海康威视", "roe": 15.0}
    analysis = {"trade_strategy": {"signal": "AVOID"}}
    assert check_signal_avoid(stock, analysis) is True


def test_signal_avoid_not_triggered_when_buy():
    stock = {"code": "002415", "name": "海康威视"}
    analysis = {"trade_strategy": {"signal": "BUY"}}
    assert check_signal_avoid(stock, analysis) is False


def test_signal_avoid_not_triggered_when_no_analysis():
    stock = {"code": "002415", "name": "海康威视"}
    assert check_signal_avoid(stock, None) is False


def test_roe_below_triggered():
    """规则 2: ROE < 5% → 强制调出"""
    stock = {"code": "600000", "name": "某股", "roe": 3.5}
    analysis = {}
    assert check_roe_below(stock, analysis, threshold=5) is True


def test_roe_below_not_triggered():
    stock = {"code": "600519", "name": "贵州茅台", "roe": 30.0}
    analysis = {}
    assert check_roe_below(stock, analysis, threshold=5) is False


def test_roe_below_not_triggered_when_none():
    stock = {"code": "600000", "name": "某股", "roe": None}
    assert check_roe_below(stock, {}, threshold=5) is False


def test_price_above_buyzone_triggered():
    """规则 3: 当前价 > 买入区上限 +20% → 强制调出"""
    stock = {"code": "600519", "name": "贵州茅台", "current_price": 2000.0}
    analysis = {"trade_strategy": {"buy_zone": "1500-1700"}}
    # 买入区上限 1700, +20% = 2040, 当前 2000 < 2040, 不触发
    assert check_price_above_buyzone(stock, analysis, pct=20) is False

    stock2 = {"code": "600519", "name": "贵州茅台", "current_price": 2100.0}
    # 2100 > 2040, 触发
    assert check_price_above_buyzone(stock2, analysis, pct=20) is True


def test_price_above_buyzone_no_buyzone():
    """买入区字段缺失 → 不触发"""
    stock = {"code": "600519", "current_price": 5000.0}
    analysis = {"trade_strategy": {}}
    assert check_price_above_buyzone(stock, analysis, pct=20) is False


def test_price_above_buyzone_invalid_buyzone():
    """买入区格式异常 → 不触发（容错）"""
    stock = {"code": "600519", "current_price": 5000.0}
    analysis = {"trade_strategy": {"buy_zone": "未知"}}
    assert check_price_above_buyzone(stock, analysis, pct=20) is False


def test_roe_collapse_triggered():
    """规则 4: ROE 同比下降 > 10pp → 强制调出"""
    stock = {
        "code": "600000", "name": "某股",
        "roe": 8.0,
        "_history_roe": {"last_year": 20.0}  # 去年 20%, 今年 8%, 下降 12pp
    }
    analysis = {}
    assert check_roe_collapse(stock, analysis, pp=10) is True


def test_roe_collapse_not_triggered():
    stock = {
        "code": "600519", "name": "贵州茅台",
        "roe": 28.0,
        "_history_roe": {"last_year": 30.0}  # 下降 2pp, 未达 10pp
    }
    assert check_roe_collapse(stock, {}, pp=10) is False


def test_roe_collapse_no_history():
    """无历史 ROE → 不触发"""
    stock = {"code": "600519", "roe": 8.0, "_history_roe": None}
    assert check_roe_collapse(stock, {}, pp=10) is False


def test_apply_hard_rules_collects_all_triggers():
    """多个硬规则同时触发 → 全部收集"""
    reviewer = WatchlistReviewer({
        "ai_review": {"hard_rules": {
            "signal_avoid": True,
            "roe_below": 5,
            "price_above_buyzone_pct": 20,
            "roe_collapse_pp": 10,
        }}
    })
    current = [
        {"code": "002415", "name": "海康威视", "roe": 15.0,
         "current_price": 100.0, "_history_roe": {"last_year": 30.0}},
    ]
    analyses = {
        "002415": {"trade_strategy": {"signal": "AVOID", "buy_zone": "50-80"}},
    }
    # 002415: Signal=AVOID ✓, ROE=15 不触发, 价格=100 vs 80*1.2=96 触发, ROE 下降 15pp 触发
    forced_out = reviewer._apply_hard_rules(current, analyses)
    codes = {f['code'] for f in forced_out}
    assert "002415" in codes
    # 触发原因列表不为空
    reasons = next(f['reasons'] for f in forced_out if f['code'] == '002415')
    assert len(reasons) >= 2  # 至少触发 2 条


def test_build_prompt_contains_required_sections():
    """prompt 必须包含角色 / 规则 / 当前观察池 / 候选池 / 大盘 / JSON schema"""
    reviewer = WatchlistReviewer({
        "ai_review": {"hard_rules": {
            "signal_avoid": True, "roe_below": 5,
            "price_above_buyzone_pct": 20, "roe_collapse_pp": 10,
        }, "watchlist_size": 5}
    })
    current = [
        {"code": "600519", "name": "贵州茅台", "roe": 30.0,
         "added_reason": "ROE 持续 30%+", "ai_confidence": "高",
         "review_count": 3},
        {"code": "000792", "name": "盐湖股份", "roe": 15.0,
         "added_reason": "低估值", "ai_confidence": "中",
         "review_count": 1},
    ]
    candidates = [
        {"code": "002415", "name": "海康威视", "roe": 20.0, "score": 75.0},
        {"code": "300750", "name": "宁德时代", "roe": 18.0, "score": 70.0},
    ]
    analyses = {
        "600519": {"trade_strategy": {"signal": "HOLD", "buy_zone": "1500-1700"}},
        "000792": {"trade_strategy": {"signal": "BUY", "buy_zone": "15-20"}},
    }
    market = [{"index_name": "上证综指", "current_value": 3174.0,
               "change_percent": 0.5}]
    forced_out = [
        {"code": "000792", "name": "盐湖股份", "reasons": ["signal_avoid"]}
    ]

    prompt = reviewer._build_prompt(
        current, candidates, analyses, market, forced_out
    )

    # 必需内容检查
    assert "价值投资基金经理" in prompt  # 角色
    assert "600519" in prompt  # 当前观察池
    assert "002415" in prompt  # 候选池
    assert "上证综指" in prompt  # 大盘
    assert "000792" in prompt  # 强制调出
    assert "watchlist_actions" in prompt  # JSON schema
    assert "new_watchlist" in prompt
    assert "journal" in prompt
    assert "5" in prompt  # watchlist_size


def test_build_prompt_initial_mode():
    """首次启动（当前观察池为空）→ prompt 切换为初始化模式"""
    reviewer = WatchlistReviewer({"ai_review": {"hard_rules": {}}})
    candidates = [
        {"code": "600519", "name": "贵州茅台", "roe": 30.0, "score": 80.0},
    ]
    prompt = reviewer._build_prompt(
        current=[], candidates=candidates, analyses={},
        market=[], forced_out=[]
    )
    assert "初始化" in prompt
    assert "从候选池选 5 只" in prompt


def test_build_prompt_contains_watch_clarification():
    """_build_prompt 包含 watch 动作判断标准"""
    reviewer = WatchlistReviewer({"ai_review": {"hard_rules": {}}})
    current = [
        {"code": "600519", "name": "贵州茅台", "roe": 25.0, "review_count": 3}
    ]
    candidates = [
        {"code": "002415", "name": "海康威视", "roe": 20.0, "score": 75.0}
    ]
    analyses = {
        "600519": {"trade_strategy": {"signal": "HOLD", "buy_zone": "1500-1700"}}
    }
    market = [{"index_name": "上证综指", "current_value": 3174.0, "change_percent": 0.5}]
    forced_out = []

    prompt = reviewer._build_prompt(
        current, candidates, analyses, market, forced_out
    )

    # 检查 watch 判断标准
    assert "watch 动作判断标准" in prompt
    assert "基本面明显恶化但未达硬规则调出线" in prompt
    assert "或需等待事件确认" in prompt


def test_coverage_validation_logic():
    """测试覆盖率校验逻辑"""
    from src.analyzer.watchlist_reviewer import WatchlistReviewer
    
    reviewer = WatchlistReviewer({"ai_review": {}})
    
    # 模拟覆盖率检查
    content_md = "# 本周复盘\\n## 池变动\\n调入 000792 盐湖股份\\n## 逐股分析\\n000792: ..."
    new_codes = {"000792", "600519"}
    
    # 检查覆盖率：确保每只在池股票都在 journal 中出现
    coverage_missing = []
    if content_md:
        for code in new_codes:
            if code not in content_md:
                coverage_missing.append(code)
    
    assert "600519" in coverage_missing
    assert "000792" not in coverage_missing


def test_call_llm_parses_valid_json(monkeypatch):
    """_call_llm 正常返回 JSON 时能解析"""
    reviewer = WatchlistReviewer({"ai_review": {}, "ai": {
        "api_base": "https://example.com/v1", "model": "test-free"
    }})

    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "choices": [{"message": {"content": json.dumps({
                    "watchlist_actions": [],
                    "new_watchlist": [],
                    "journal": {"title": "测试", "content_md": "# 测试"}
                })}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, headers=None, json=None):
            return FakeResponse()

    import httpx
    monkeypatch.setattr(httpx, "Client", FakeClient)
    # 跳过 FreeModelPool acquire，直接返回 model 名
    monkeypatch.setattr(reviewer, "_resolve_model", lambda: "test-free")

    result = reviewer._call_llm("test prompt")
    assert result is not None
    assert "watchlist_actions" in result
    assert result["journal"]["title"] == "测试"


def test_review_initial_mode_persists_5_stocks(monkeypatch, tmp_path):
    """首次启动：观察池为空 → AI 选 5 只 → 落库"""
    import os
    from src.models import database as db_mod
    # 用临时数据库
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    # 预置 screening_result（候选池数据源，run_date 取近 3 天防时间腐）
    from src.utils import now_cn
    from datetime import timedelta
    recent = (now_cn() - timedelta(days=3)).strftime("%Y-%m-%d")
    from src.models.database import ScreeningResultDAO
    ScreeningResultDAO().save_batch([
        {"run_id": "r1", "run_date": recent, "code": "600519",
         "name": "贵州茅台", "score": 85, "pe": 30, "pb": 10,
         "roe": 30, "gross_margin": 90, "net_margin": 50,
         "ocf_per_share": 50, "revenue_growth": 15, "profit_growth": 20,
         "debt_ratio": 20, "market_cap": 2000, "reason": "测试"},
    ])

    reviewer = WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "watchlist_size": 5,
                      "hard_rules": {}},
        "ai": {"api_base": "https://example.com", "model": "test-free"}
    })

    # mock LLM 返回
    def fake_call_llm(prompt):
        return {
            "watchlist_actions": [
                {"code": "600519", "action": "add", "reason": "ROE 30%+"}
            ],
            "new_watchlist": ["600519"],
            "journal": {
                "title": "首次复盘",
                "content_md": "# 首次复盘\n初始化观察池"
            }
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    result = reviewer.review("run_test_001")

    from src.models.ai_watchlist import AiWatchlistDAO, AiJournalDAO
    watchlist = AiWatchlistDAO().get_all()
    assert len(watchlist) == 1
    assert watchlist[0]['code'] == "600519"
    assert watchlist[0]['added_reason'] == "ROE 30%+"

    journal = AiJournalDAO().get_latest()
    assert journal is not None
    assert journal['title'] == "首次复盘"
    assert journal['run_id'] == "run_test_001"

    # 返回值结构
    assert "new_watchlist" in result
    assert "journal" in result
    assert result["new_watchlist"] == ["600519"]


def test_review_watch_action_persists_status(monkeypatch, tmp_path):
    """watch 动作：留池 + 状态/期限落库 + 覆盖缺失记账（B6a/B7）"""
    import json
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.utils import now_cn
    from datetime import timedelta
    recent = (now_cn() - timedelta(days=3)).strftime("%Y-%m-%d")
    from src.models.database import ScreeningResultDAO
    ScreeningResultDAO().save_batch([
        {"run_id": "r1", "run_date": recent, "code": "600519",
         "name": "贵州茅台", "score": 85, "pe": 30, "pb": 10,
         "roe": 30, "gross_margin": 90, "net_margin": 50,
         "ocf_per_share": 50, "revenue_growth": 15, "profit_growth": 20,
         "debt_ratio": 20, "market_cap": 2000, "reason": "测试"},
    ])
    from src.models.ai_watchlist import AiWatchlistDAO, AiJournalDAO
    AiWatchlistDAO().add("600519", "贵州茅台", "ROE 30%+")

    reviewer = WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "watchlist_size": 5,
                      "hard_rules": {}},
        "ai": {"api_base": "https://example.com", "model": "test-free"}
    })

    def fake_call_llm(prompt):
        return {
            "watchlist_actions": [
                {"code": "600519", "action": "watch",
                 "reason": "毛利率拐点待确认", "watch_until": "2026-10-15"}
            ],
            "new_watchlist": ["600519"],
            "journal": {
                "title": "复盘",
                "content_md": "# 复盘\n无变动"  # 故意不提代码，触发覆盖缺失
            }
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    reviewer.review("run_test_watch")

    row = AiWatchlistDAO().get_by_code("600519")
    assert row['status'] == 'watch'
    assert row['status_reason'] == "毛利率拐点待确认"
    assert row['watch_until'] == "2026-10-15"
    assert len(AiWatchlistDAO().get_all()) == 1  # watch 仍在池
    summ = json.loads(AiJournalDAO().get_latest()['actions_summary'])
    assert summ['coverage_missing'] == ["600519"]
    assert summ['watch'] == 1


def test_review_rejects_candidate_not_in_pool(monkeypatch, tmp_path):
    """LLM 调入未在候选池的股票 → 拒绝调入"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.models.database import ScreeningResultDAO
    from src.models.ai_watchlist import AiWatchlistDAO
    ScreeningResultDAO().save_batch([
        {"run_id": "r1", "run_date": "2026-07-15", "code": "600519",
         "name": "贵州茅台", "score": 85, "pe": 30, "pb": 10, "roe": 30,
         "gross_margin": 90, "net_margin": 50, "ocf_per_share": 50,
         "revenue_growth": 15, "profit_growth": 20, "debt_ratio": 20,
         "market_cap": 2000, "reason": "测试"},
    ])
    # 预置当前观察池
    AiWatchlistDAO().add("600519", "贵州茅台", "测试")

    reviewer = WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "hard_rules": {}},
        "ai": {}
    })

    # LLM 尝试调入不在候选池的 999999
    def fake_call_llm(prompt):
        return {
            "watchlist_actions": [
                {"code": "600519", "action": "keep", "reason": "稳定"},
                {"code": "999999", "action": "add", "reason": "未知股"}
            ],
            "new_watchlist": ["600519", "999999"],
            "journal": {"title": "T", "content_md": "C"}
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    result = reviewer.review("run_test_002")

    # 999999 被拒绝
    watchlist = AiWatchlistDAO().get_all()
    codes = {w['code'] for w in watchlist}
    assert "999999" not in codes
    assert "600519" in codes


def test_review_forced_out_overrides_llm(monkeypatch, tmp_path):
    """硬规则强制调出的股票，即使 LLM 想保留，也必须调出"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.models.database import ScreeningResultDAO
    from src.models.ai_watchlist import AiWatchlistDAO
    ScreeningResultDAO().save_batch([
        {"run_id": "r1", "run_date": "2026-07-15", "code": "600519",
         "name": "贵州茅台", "score": 85, "pe": 30, "pb": 10, "roe": 30,
         "gross_margin": 90, "net_margin": 50, "ocf_per_share": 50,
         "revenue_growth": 15, "profit_growth": 20, "debt_ratio": 20,
         "market_cap": 2000, "reason": "测试"},
    ])
    # 当前观察池有一只恶化的股票 002415
    AiWatchlistDAO().add("002415", "海康威视", "测试")
    # 预置它的周五 AI 分析（Signal=AVOID）
    from src.models.database import StockAnalysisHistoryDAO
    StockAnalysisHistoryDAO().save(
        "002415", "r1", 70.0,
        json.dumps({"analysis": "测试"}),
        json.dumps({"signal": "AVOID"})
    )

    reviewer = WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "hard_rules": {
            "signal_avoid": True, "roe_below": 5,
            "price_above_buyzone_pct": 20, "roe_collapse_pp": 10,
        }},
        "ai": {}
    })

    # LLM 想保留 002415（违反硬规则）
    def fake_call_llm(prompt):
        return {
            "watchlist_actions": [
                {"code": "002415", "action": "keep", "reason": "稳定"}
            ],
            "new_watchlist": ["002415"],
            "journal": {"title": "T", "content_md": "C"}
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    result = reviewer.review("run_test_003")

    # 002415 被强制调出
    watchlist = AiWatchlistDAO().get_all()
    codes = {w['code'] for w in watchlist}
    assert "002415" not in codes


def test_review_skips_when_no_candidates(monkeypatch, tmp_path):
    """候选池为空 → 跳过复盘"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()

    reviewer = WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "hard_rules": {}},
        "ai": {}
    })
    call_count = [0]
    def fake_call_llm(prompt):
        call_count[0] += 1
        return {}
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    result = reviewer.review("run_test_004")
    assert call_count[0] == 0  # 没调用 LLM
    assert result.get("skipped") is True


def test_veto_triggered_count():
    """规则 5: veto_checklist.triggered_count >= 1 → 强制调出"""
    stock = {"code": "600000", "name": "某股"}
    analysis = {"veto_checklist": {"triggered_count": 1,
                                    "management_integrity_issue": True}}
    assert check_veto_triggered(stock, analysis) is True


def test_veto_triggered_by_any_red_line():
    """triggered_count 缺失/异常时，任一红线 true 也触发"""
    stock = {"code": "600000", "name": "某股"}
    analysis = {"veto_checklist": {"cannot_write_200_char_thesis": True}}
    assert check_veto_triggered(stock, analysis) is True


def test_veto_not_triggered_clean():
    """8 条全 false → 不触发"""
    stock = {"code": "600519", "name": "贵州茅台"}
    analysis = {"veto_checklist": {"triggered_count": 0}}
    assert check_veto_triggered(stock, analysis) is False


def test_veto_not_triggered_no_analysis():
    """无分析数据 → 不触发"""
    stock = {"code": "600519", "name": "贵州茅台"}
    assert check_veto_triggered(stock, None) is False


def test_veto_not_triggered_no_veto_field():
    """analysis 无 veto_checklist 字段 → 不触发（旧数据兼容）"""
    stock = {"code": "600519", "name": "贵州茅台"}
    analysis = {"trade_strategy": {"signal": "BUY"}}
    assert check_veto_triggered(stock, analysis) is False
