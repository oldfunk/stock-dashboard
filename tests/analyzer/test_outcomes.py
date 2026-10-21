"""前向收益纯函数单测（2026-10-21）。

重点验证口径，而不是覆盖率：
- 入场严格晚于分析日（无前视）
- 未到期/缺基准一律 None，不填 0
- 超额 = 个股 - 基准
- BUY 跑赢 / AVOID 跑输 才算命中，HOLD 不评命中率
"""

import json

from src.analyzer.outcomes import (
    aggregate_outcomes,
    compute_forward_outcome,
    extract_signal,
    is_hit,
)


def _bars(pairs):
    return [{"trade_date": d, "close": c} for d, c in pairs]


class TestComputeForwardOutcome:
    def test_entry_is_first_trading_day_after_analysis(self):
        stock = _bars([
            ("2026-09-29", 100.0),   # 分析日当天，不能用来入场
            ("2026-09-30", 110.0),   # T+1 入场
            ("2026-10-09", 121.0),
        ])
        bench = _bars([
            ("2026-09-29", 100.0),
            ("2026-09-30", 100.0),
            ("2026-10-09", 105.0),
        ])
        out = compute_forward_outcome(
            "2026-09-29", stock, bench, horizons=(1,))
        assert out["entry_date"] == "2026-09-30"
        assert out["entry_price"] == 110.0
        assert out["ret_1"] == 10.0
        assert out["bench_1"] == 5.0
        assert out["excess_1"] == 5.0

    def test_no_lookahead_when_only_analysis_day_bar(self):
        out = compute_forward_outcome(
            "2026-09-29", _bars([("2026-09-29", 100.0)]),
            _bars([("2026-09-29", 100.0)]), horizons=(1,))
        assert out["entry_date"] is None
        assert out["ret_1"] is None

    def test_incomplete_horizon_is_none_not_zero(self):
        stock = _bars([("2026-09-30", 10.0 + i) for i in range(30)])
        bench = _bars([("2026-09-30", 100.0 + i) for i in range(30)])
        out = compute_forward_outcome(
            "2026-09-29", stock, bench, horizons=(20, 60, 120))
        assert out["ret_20"] is not None
        assert out["ret_60"] is None
        assert out["ret_120"] is None
        assert "ret_60" in out and out["ret_60"] is None

    def test_benchmark_missing_exact_date_leaves_excess_none(self):
        stock = _bars([
            ("2026-09-30", 10.0),
            ("2026-10-09", 11.0),
        ])
        bench = _bars([("2026-09-30", 100.0)])  # 缺退出日
        out = compute_forward_outcome(
            "2026-09-29", stock, bench, horizons=(1,))
        assert out["ret_1"] == 10.0
        assert out["bench_1"] is None
        assert out["excess_1"] is None

    def test_bad_prices_are_ignored(self):
        stock = _bars([
            ("2026-09-30", 10.0),
            ("2026-10-09", 0.0),
            ("2026-10-10", None),
            ("2026-10-13", 12.0),
        ])
        out = compute_forward_outcome(
            "2026-09-29", stock, [], horizons=(1, 2))
        # 0 和 None 被剔除后，有效序列是 09-30、10-13
        assert out["entry_date"] == "2026-09-30"
        assert out["ret_1"] == 20.0
        assert out["ret_2"] is None


class TestExtractSignal:
    def test_parses_trade_and_verdict(self):
        row = {
            "ai_trade_strategy": json.dumps(
                {"signal": "buy", "confidence": "高"}),
            "ai_analysis": json.dumps({"verdict": "通过"}),
        }
        assert extract_signal(row) == ("BUY", "高", "通过")

    def test_bad_json_does_not_guess(self):
        row = {"ai_trade_strategy": "{bad", "ai_analysis": "纯文本"}
        assert extract_signal(row) == ("", "", "")

    def test_missing_fields(self):
        assert extract_signal({}) == ("", "", "")


class TestHit:
    def test_buy_hits_on_positive_excess(self):
        assert is_hit("BUY", 1.0) is True
        assert is_hit("BUY", -1.0) is False

    def test_avoid_hits_on_negative_excess(self):
        assert is_hit("AVOID", -2.0) is True
        assert is_hit("AVOID", 2.0) is False

    def test_hold_and_unknown_have_no_hit(self):
        assert is_hit("HOLD", 5.0) is None
        assert is_hit("", 5.0) is None
        assert is_hit("BUY", None) is None


class TestAggregate:
    def _rows(self):
        return [
            {"stock_code": "A", "signal": "BUY", "confidence": "高",
             "ret_20": 6.0, "bench_20": 1.0, "excess_20": 5.0},
            {"stock_code": "B", "signal": "BUY", "confidence": "高",
             "ret_20": -1.0, "bench_20": 1.0, "excess_20": -2.0},
            {"stock_code": "A", "signal": "AVOID", "confidence": "中",
             "ret_20": -5.0, "bench_20": -1.0, "excess_20": -4.0},
            {"stock_code": "C", "signal": "HOLD", "confidence": "低",
             "ret_20": 2.0, "bench_20": 1.0, "excess_20": 1.0},
            {"stock_code": "D", "signal": "BUY", "confidence": "高",
             "ret_20": 9.0, "bench_20": 1.0, "excess_20": None},
        ]

    def test_group_by_signal(self):
        groups = {g["signal"]: g for g in aggregate_outcomes(self._rows(), 20)}
        assert groups["BUY"]["n"] == 2          # 未到期的那条不计入
        assert groups["BUY"]["stocks"] == 2
        assert groups["BUY"]["avg_excess"] == 1.5
        assert groups["BUY"]["hit_rate"] == 50.0
        assert groups["AVOID"]["hit_rate"] == 100.0
        assert groups["HOLD"]["hit_rate"] is None

    def test_group_by_signal_and_confidence(self):
        groups = aggregate_outcomes(self._rows(), 20, by_confidence=True)
        keys = {(g["signal"], g["confidence"]) for g in groups}
        assert ("BUY", "高") in keys
        assert ("HOLD", "低") in keys
        buy = [g for g in groups if g["signal"] == "BUY"][0]
        assert buy["n"] == 2 and buy["hit_rate"] == 50.0

    def test_all_pending_produces_empty_list(self):
        rows = [{"stock_code": "A", "signal": "BUY", "confidence": "高",
                 "excess_20": None}]
        assert aggregate_outcomes(rows, 20) == []
