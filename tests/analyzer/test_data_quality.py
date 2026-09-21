"""数据质量标注单测（分析师 critique #1：AI 必须知道数字的来源/日期/置信度）。

覆盖：
- _data_quality_facts：全字段/全缺失/坏值永不抛异常
- _data_quality_text：关键行渲染（ST/缺失/大盘/上市年限）
- analyze_stock：prompt 注入质量块 + 结果落库 data_quality
- _attach_batch_context：run_id 回填 + 大盘挂载（失败不阻断）
"""

import json


def _full_stock():
    return {
        "code": "600519", "name": "贵州茅台",
        "pe": 22.0, "pb": 8.0, "roe": 30.0,
        "revenue_growth": 15.0, "profit_growth": 18.0,
        "debt_ratio": 20.0, "gross_margin": 91.0, "ocf_per_share": 12.0,
        "roe_5y_avg": 29.0, "fcf_5y_sum": 1e11, "roic_5y_avg": 25.0,
        "sector": "白酒",
        "run_id": "20260921_153011", "run_date": "2026-09-21",
        "snapshot_date": "2026-09-19", "financial_updated_at": "2026-09-20",
        "data_years": "2016-2026", "roe_5y_count": 5,
        "list_date": "2001-08-27", "is_st": False,
        "_market": [
            {"index_name": "上证指数", "change_percent": -1.2},
            {"index_name": "创业板指", "change_percent": None},
            "坏记录",
            {"index_name": "沪深300", "change_percent": "abc"},
        ],
    }


def test_facts_full():
    from src.analyzer.ai_analyzer import _data_quality_facts
    f = _data_quality_facts(_full_stock(), today="2026-09-21")
    assert f["analysis_date"] == "2026-09-21"
    assert f["data_years"] == "2016-2026"
    assert f["roe_years"] == 5
    assert 24.0 < f["listed_years"] < 26.0
    assert f["is_st"] is False
    assert f["missing"] == []
    assert f["market"][0] == "上证指数 -1.20%"
    assert f["market"][1] == "创业板指 未知"
    assert len(f["market"]) == 3  # 坏记录被跳过


def test_facts_empty_never_crashes():
    from src.analyzer.ai_analyzer import _data_quality_facts, _data_quality_text
    f = _data_quality_facts({}, today="2026-09-21")
    assert f["analysis_date"] == "2026-09-21"
    assert f["is_st"] is None
    assert f["listed_years"] is None
    assert f["market"] is None
    assert len(f["missing"]) > 5
    t = _data_quality_text(f)
    assert "未知" in t
    assert "数据不足" in t


def test_facts_st_and_run_id_date():
    from src.analyzer.ai_analyzer import _data_quality_facts, _data_quality_text
    s = {"code": "000001", "is_st": 1, "run_id": "20260920_000012",
         "pe": None, "sector": None}
    f = _data_quality_facts(s, today="2026-09-21")
    assert f["analysis_date"] == "2026-09-20"  # 从 run_id 解析
    assert f["is_st"] is True
    assert "PE" in f["missing"] and "行业" in f["missing"]
    t = _data_quality_text(f)
    assert "ST" in t and "PE" in t


def _valid_doc():
    return json.dumps({
        "analysis": "生意极好，现金流充沛，值得长期跟踪持有不动摇。",
        "moat_evaluation": [
            {"type": "品牌", "score": 4, "trend": "稳定", "evidence": "x"},
            {"type": "网络效应", "score": 2, "trend": "稳定", "evidence": "x"},
            {"type": "无形资产", "score": 3, "trend": "稳定", "evidence": "x"},
            {"type": "成本优势", "score": 4, "trend": "稳定", "evidence": "x"},
            {"type": "有效规模", "score": 2, "trend": "稳定", "evidence": "x"},
        ],
        "management_score": {"capital_allocation": 7,
                             "shareholder_friendliness": 6,
                             "summary": "稳健"},
        "intrinsic_value": {"conservative": "1000亿", "base_case": "1500亿",
                            "optimistic": "2000亿", "method": "自选：相对估值"},
        "investment_strategy": "长期持有",
        "trade_strategy": {"signal": "HOLD", "confidence": "中"},
        "verdict": "灰色地带：估值合理但无折价",
        "veto_checklist": {"triggered_count": 0},
        "mirror_test": {"passed": True},
        "checklist": {},
        "reverse_thinking": "若消费降级则承压",
    }, ensure_ascii=False)


def test_analyze_stock_injects_quality_and_records(monkeypatch):
    """prompt 含质量块；结果带 data_quality 落库快照"""
    import src.analyzer.ai_analyzer as mod
    monkeypatch.setattr(mod, "_build_history_summary", lambda *a, **k: "")
    prompts = []

    def fake_call(prompt):
        prompts.append(prompt)
        return _valid_doc(), "test-model", None

    from src.analyzer.ai_analyzer import AiAnalyzer
    an = AiAnalyzer({"model": "test-free"})
    monkeypatch.setattr(an, "_call_llm", fake_call)
    stock = _full_stock()
    stock["score"] = 88.0
    result = an.analyze_stock(stock)
    assert result is not None
    assert "【数据质量与背景" in prompts[0]
    assert "2016-2026" in prompts[0]
    assert "ST状态" in prompts[0]
    assert result["data_quality"]["data_years"] == "2016-2026"
    assert result["model"] == "test-model"


def test_attach_batch_context(monkeypatch):
    """run_id 回填 + 大盘挂载；DAO 失败不阻断"""
    import src.analyzer.ai_analyzer as mod

    class FakeMarket:
        def get_latest(self):
            return [{"index_name": "上证指数", "change_percent": 0.5}]

    monkeypatch.setattr("src.models.database.MarketIndexDAO", FakeMarket)
    stocks = [{"code": "600519"}, {"code": "000001", "run_id": "keep"}]
    mod._attach_batch_context(stocks, "20260921_153011")
    assert stocks[0]["run_id"] == "20260921_153011"
    assert stocks[1]["run_id"] == "keep"
    assert stocks[0]["_market"][0]["index_name"] == "上证指数"


def test_attach_batch_context_dao_failure(monkeypatch):
    import src.analyzer.ai_analyzer as mod

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("db down")

    monkeypatch.setattr("src.models.database.MarketIndexDAO", Boom)
    stocks = [{"code": "600519"}]
    mod._attach_batch_context(stocks, "r1")  # 不抛异常
    assert stocks[0]["run_id"] == "r1"
    assert stocks[0]["_market"] is None


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
