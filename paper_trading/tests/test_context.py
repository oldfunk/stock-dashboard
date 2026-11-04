"""上下文压缩（T63 重写分级）：预算可达、持仓细节不动、provenance 永不截断。

改测试的理由（不是"为了让它绿"）：旧用例锁的是旧阶梯的具体数字
（股息留 5 条、收盘留 10 根），而 11-03 现网实测证明那套阶梯**裁不到体积**——
预算 8000/6000/4000/3000 输出都是 97,912 字符一字节没变。旧用例之所以全绿，
是因为它的假数据把大头放在了会被裁的字段上，与真实分布相反。

现在锁的是四条规矩（每条都对应一个会被咬到的场景）：
1. provenance 与 account/positions/risk_control 永不裁——模型分不清这些就会瞎猜；
2. 分级以**持仓**为准：池内候选的股息史/追踪/论点先走，持仓的细节留着；
3. 压得进预算就压进；压不进就**如实报** `within_budget=False`（不许假装成功）；
4. `compress_context` 未超预算时返回原对象（决策路径靠这个不复制大 dict）。
"""
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent.context import (  # noqa: E402
    CARD_CAPS_HELD, CARD_CAPS_POOL, FIN_KEYS_POOL, compress_context,
    estimate_tokens, held_codes, shrink_to_budget, size_of, truncate)


def _card(name: str, *, with_finn: bool = True) -> dict:
    """造一张"接近真实分布"的卡：大头在股息史/追踪/论点（现网 26,850/11,590/…）。"""
    return {
        "fundamentals": {"name": name, "sector": "白酒", "pe": 25.1, "pb": 8.0,
                         "market_cap_yi": 1800.0, "roe": 6.2},
        "financial_summary": ({"roe_5y_avg": 21.9, "roe_10y_avg": 23.9,
                               "gross_margin_5y_avg": 25.2, "net_margin_5y_avg": 9.1,
                               "ocf_latest": 7.02, "debt_ratio_latest": 61.1,
                               "roic_5y_avg": 12.5, "data_years": "2016-2025",
                               "junk": "x" * 500} if with_finn else {}),
        "dividend": [{"stock_code": "600519", "report_date": f"20{10+i}-12-31",
                      "cash_per_share": 1.6, "eps": 3.9, "source": "eastmoney"}
                     for i in range(10)],
        "outcomes": [{"analysis_date": f"20{10+i}-06-30", "analysis": "长" * 300}
                     for i in range(10)],
        "ai_analysis": {"analysis": "长" * 2000, "trade_strategy": "短" * 500},
        "thesis": {"core": "论" * 1000, "sell_conditions": "卖" * 1000},
    }


def _ctx(held=("600519",), pool=("600519", "000858", "300390", "601872")):
    return {
        "account": {"cash": 67422.96, "total_value": 100189.96, "pnl": 189.96},
        "positions": [{"stock": f"贵州茅台({held[0]})", "total": 100, "available": 0,
                       "avg_cost": 1500.0, "price": 1600.0, "market_value": 160000.0}],
        "risk_control": {"peak_total_value": 100189.96, "drawdown_pct": 0.0,
                         "drawdown_halt": False, "daily_loss_halt": False},
        "recent_closes": {f"名称({s})": [{"date": f"2026-11-0{i}", "close": 1.0 + i}
                                         for i in range(1, 6)] for s in pool},
        "recent_nav": [{"time": f"2026-11-0{i}T16:00", "total": 100000.0, "pnl": 0.0}
                       for i in range(1, 6)],
        "recent_ops": [{"time": f"2026-11-03T09:{i:02d}", "action": "engine:buy",
                        "params": "p" * 300, "result": "r" * 400, "ok": True}
                       for i in range(1, 11)],
        "value_view": {"stock_cards": {f"名称({s})": _card(f"公司{s}") for s in pool},
                       "screening": {"rows": [{"code": s, "reason": "理" * 1000,
                                               "score_detail": "d" * 1000}
                                              for s in pool]},
                       "market_regime": {"temperature": 62}},
        "news": {"source": "mother:news", "items": [
            {"title": f"新闻{i}", "summary": "新" * 1000, "ai_brief": "快" * 1000,
             "published_at": f"2026-11-03T0{i}:00"} for i in range(1, 25)]},
        "data_provenance": {"regime": {"source": "mother:market_index",
                                       "asof": "2026-11-03", "stale": False}},
        "legend": {"positions": "真实持仓（唯一权威口径）"},
        "note": "模拟盘。",
    }


def test_estimate_tokens_and_truncate():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("中文") == 2
    assert truncate("x" * 100, 10).endswith("…[截断]")


def test_held_codes_reads_the_authoritative_positions_block():
    """分级以持仓为准 ⇒ "谁是持仓"必须从 positions 认出来，认不出要保守回空集。"""
    assert held_codes(_ctx()) == {"600519"}
    assert held_codes({"positions": [{"stock": "无代码"}]}) == set()
    assert held_codes({}) == set()


def test_under_budget_returns_the_same_object_not_a_copy():
    ctx = _ctx()
    out, meta = shrink_to_budget(ctx, 999_999)
    assert out is ctx
    assert meta["within_budget"] is True and meta["dropped"] == []
    assert compress_context(ctx, 999_999) is ctx      # 决策路径不复制大 dict


def test_compression_actually_reaches_the_budget_and_stops_at_the_floor():
    """这一条就是 T64 说的"knob 定义了也读了、但改变不了行为"的反面证据：
    预算不同，输出体积必须不同——旧实现在现网四种预算下体积完全相同。

    到地板（账户/持仓/熔断态/provenance/持仓细节 + 收盘与流水）就不再往下裁：
    再压要动的东西比"上下文超预算"本身更贵。到不了预算必须如实说。
    """
    ctx = _ctx()
    seen = []
    for budget in (999_999, 8000, 6000):
        _out, meta = shrink_to_budget(ctx, budget)
        assert meta["within_budget"] is True, meta
        seen.append(meta["after_tokens"])
    assert seen == sorted(seen, reverse=True) and len(set(seen)) == 3

    floor = None
    for budget in (4000, 900, 40):
        _out, meta = shrink_to_budget(ctx, budget)
        assert meta["within_budget"] is False, meta
        assert "仍超预算" in meta["reason"] and meta["dropped"], meta
        floor = floor or meta["after_tokens"]
        assert meta["after_tokens"] == floor        # 地板是稳定的，不是越压越丢


def test_pool_long_history_goes_first_held_detail_stays():
    ctx = _ctx(held=("600519",))
    out, _meta = shrink_to_budget(ctx, 8000)
    cards = out["value_view"]["stock_cards"]
    held_card = cards["名称(600519)"]
    pool_card = cards["名称(000858)"]
    # 持仓：股息/追踪按 HELD 配额保留，AI 分析与论点还在
    assert len(held_card["dividend"]) == CARD_CAPS_HELD["dividend"]
    assert len(held_card["outcomes"]) == CARD_CAPS_HELD["outcomes"]
    assert len(held_card["ai_analysis"]["analysis"]) <= CARD_CAPS_HELD["ai_analysis_chars"] + 6
    assert "thesis" in held_card
    # 池内候选：股息史/追踪/论点整段丢弃（现网这块占 26,850 字符，问一次都没用到）
    assert "dividend" not in pool_card and "outcomes" not in pool_card
    assert "thesis" not in pool_card
    assert set(pool_card["financial_summary"]) <= set(FIN_KEYS_POOL)
    assert "junk" not in pool_card["financial_summary"]      # 白名单外的键一律不进上下文


def test_provenance_account_positions_and_risk_control_are_never_cut():
    """这三段被裁 = 模型开始瞎猜（11-02 "为什么没交易"没人能答的那个形状）。"""
    ctx = _ctx()
    out, _meta = shrink_to_budget(ctx, 1200)        # 压到极限也要留着
    assert out["data_provenance"] == ctx["data_provenance"]
    assert out["account"] == ctx["account"]
    assert out["positions"] == ctx["positions"]
    assert out["risk_control"] == ctx["risk_control"]
    assert out["legend"] == ctx["legend"]


def test_closes_and_news_and_screening_are_capped():
    from paper_trading.agent.context import (_NEWS_ITEMS, _POOL_CLOSES, _SCREEN_ROWS)

    out, _meta = shrink_to_budget(_ctx(), 8000)
    assert len(out["recent_closes"]["名称(000858)"]) == _POOL_CLOSES
    assert len(out["recent_closes"]["名称(600519)"]) <= 5      # 持仓留 5 根
    assert len(out["news"]["items"]) <= _NEWS_ITEMS
    assert len(out["value_view"]["screening"]["rows"]) <= _SCREEN_ROWS
    assert out["news"]["items"][0]["summary"].endswith("…[截断]")
    for r in out["value_view"]["screening"]["rows"]:
        assert "score_detail" not in r


def test_drop_order_is_visible_and_recorded():
    """丢了什么必须说得出来——"清干净了"和"压好了"一样不许是猜的。"""
    out, meta = shrink_to_budget(_ctx(), 900)
    assert meta["dropped"], meta
    assert meta["dropped"][0] == "pool_fundamentals"      # 先丢非持仓的基本面
    assert "news" in meta["dropped"]
    assert "名称(000858)" in json.dumps(out["value_view"]["stock_cards"],
                                       ensure_ascii=False) or \
        meta["dropped"][-1] == "pool_cards"


def test_impossible_budget_reports_honestly_instead_of_lying():
    """压不进去就说压不进去（纪律 4：宁可响，不许静默假装成功）。"""
    _out, meta = shrink_to_budget(_ctx(), 40)
    assert meta["within_budget"] is False
    assert "仍超预算" in meta["reason"]
    assert meta["after_tokens"] > 40


def test_size_of_and_meta_shape_for_op_log_trail():
    ctx = _ctx()
    out, meta = shrink_to_budget(ctx, 8000)
    for key in ("budget", "before_chars", "before_tokens", "after_chars",
                "after_tokens", "caps", "dropped", "within_budget", "reason"):
        assert key in meta, key
    assert meta["before_chars"] == size_of(ctx)
    assert meta["after_chars"] == size_of(out)
    json.dumps(meta, ensure_ascii=False)      # 留痕必须能序列化


def test_llm_context_uses_budget_and_reports_meta(tmp_path):
    """`agent.context_budget` 真能改变上下文体积（T64 的 knob 有效性，行为级断言）。"""
    import paper_trading.cli as hb

    b = hb.TradingBridge(data_db=tempfile.mktemp(suffix=".db", dir=tmp_path),
                         account_db=tempfile.mktemp(suffix=".db", dir=tmp_path),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    b.agent_cfg = types.SimpleNamespace(context_budget=1, news_enabled=False,
                                        llm_timeout=120.0, llm_retries=1,
                                        llm_max_tokens=8192, llm_thinking="disabled")
    ctx = b.llm_context(max_ops=1, max_nav=1, closes_n=1, symbols=[])
    assert isinstance(ctx, dict) and "account" in ctx
    meta = b.last_context_meta
    assert meta and meta["reason"], meta        # 压缩有没有跑、结果如何，要说得出来


def test_llm_context_compression_failure_is_loud_not_silent(monkeypatch, tmp_path,
                                                            caplog):
    """旧写法 `except Exception: pass` 让"根本没压"这件事隐身；现在必须 warn + 留元信息。"""
    import paper_trading.agent.context as cx
    import paper_trading.cli as hb

    def boom(ctx, budget, *, held=None):
        raise RuntimeError("压缩器炸了")

    monkeypatch.setattr(cx, "shrink_to_budget", boom)
    b = hb.TradingBridge(data_db=tempfile.mktemp(suffix=".db", dir=tmp_path),
                         account_db=tempfile.mktemp(suffix=".db", dir=tmp_path),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    b.agent_cfg = types.SimpleNamespace(context_budget=100, news_enabled=False)
    ctx = b.llm_context(max_ops=1, max_nav=1, closes_n=1, symbols=[])
    assert "account" in ctx                       # 压缩坏了不拦问答
    assert "压缩失败" in (b.last_context_meta or {}).get("reason", "")
    assert "压缩失败" in caplog.text              # 日志里看得见
