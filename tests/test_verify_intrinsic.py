#!/usr/bin/env python3
"""verify_intrinsic.py 单元测试。"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from scripts.verify_intrinsic import (
    exit_pe, verify_intrinsic, _call_from_analyzer,
    currency_check, spread_check, MIN_SPREAD, CURRENCY_BANDS, LABELS,
)


class TestExitPE:
    def test_normal_case(self):
        # (1 - 0.02/0.15) / (0.07 - 0.02) = 0.8667/0.05 = 17.33
        pe = exit_pe(0.15, 0.02, 0.07)
        assert abs(pe - 17.333) < 0.01

    def test_spread_zero_returns_none(self):
        assert exit_pe(0.15, 0.07, 0.07) is None

    def test_spread_negative_returns_none(self):
        assert exit_pe(0.15, 0.08, 0.07) is None

    def test_higher_roic_higher_pe(self):
        pe_high = exit_pe(0.25, 0.02, 0.07)
        pe_low = exit_pe(0.10, 0.02, 0.07)
        assert pe_high > pe_low


class TestVerifyIntrinsic:
    def test_basic_output_shape(self):
        r = verify_intrinsic("600519", 0.15, 50.0, 18000.0)
        assert r["code"] == "600519"
        assert "terminal_pe" in r
        assert "C1" in r and "C2" in r
        for label in LABELS:
            assert label in r["terminal_pe"]

    def test_g_values_asymmetric(self):
        """上游不对称规则：悲观=基准-1.5pct，乐观=基准+1pct"""
        r = verify_intrinsic("600519", 0.15, 50.0, 18000.0)
        g = r["g_values"]
        assert abs(g[1] - g[0] - 0.015) < 1e-9  # 基准-悲观=1.5pct
        assert abs(g[2] - g[1] - 0.01) < 1e-9   # 乐观-基准=1pct

    def test_c1_fails_on_wrong_currency(self):
        r = verify_intrinsic("600519", 0.15, 50.0, 18000.0, currency="USD")
        # USD r 上限 0.115，0.075 落在 CNY 区间 → fail
        assert not r["C1"]["pass"]

    def test_c2_flags_narrow_spread(self):
        r = verify_intrinsic("600519", 0.15, 50.0, 18000.0)
        # optimistic g=2.5%, r=7.5% → spread=5.0pct 边界
        opt = [x for x in r["C2"]["results"] if x["label"] == "optimistic"][0]
        assert opt["status"] in ("ok", "窄")

    def test_c1_unknown_currency_fails(self):
        c1_pass, warns, fails = currency_check("XXX", 0.075, [0.01, 0.02, 0.03])
        assert not c1_pass
        assert len(fails) > 0

    def test_c1_passes_valid_cny(self):
        c1_pass, warns, fails = currency_check("CNY", 0.075, [0.01, 0.02, 0.03])
        assert c1_pass
        assert len(fails) == 0

    def test_spread_check_flags_失效(self):
        results, fails, warns = spread_check(0.15, [0.08, 0.07, 0.07], 0.07)
        # pessimistic g=0.08, r=0.07 → spread<=0 失效
        assert any(x["status"] == "失效" for x in results)
        assert len(fails) > 0

    def test_owner_earnings_calculation(self):
        r = verify_intrinsic("600519", 0.15, 50.0, 18000.0)
        iv = r["intrinsic_values"]
        # PE base=15, OE=50 → IV base=750
        assert abs(iv["base_case"] - 750.0) < 1


class TestCallFromAnalyzer:
    def test_with_mismatch_returns_fail(self):
        """LLM 估值远超终值验算 → mismatch_fail"""
        r = _call_from_analyzer(
            "600519", 0.15, 50.0, 18000.0,
            llm_intrinsic={"conservative": 8000, "base_case": 12000, "optimistic": 18000})
        assert any(v == "mismatch_fail" for v in r.get("verdicts", {}).values())
        assert len(r["mismatches"]) == 3

    def test_within_tolerance_no_mismatch(self):
        """LLM 估值接近终值验算 → 无 mismatch"""
        # base PE=15, OE=50 → IV=750; LLM 给 700-800 区间偏差<10%
        r = _call_from_analyzer(
            "600519", 0.15, 50.0, 18000.0,
            llm_intrinsic={"conservative": 670, "base_case": 740, "optimistic": 820})
        assert all(v != "mismatch_fail" for v in r.get("verdicts", {}).values())
        assert all(v != "mismatch_warn" for v in r.get("verdicts", {}).values())

    def test_no_llm_intrinsic_still_ok(self):
        """不传 LLM 估值 → verdicts 为空，无 mismatch"""
        r = _call_from_analyzer("600519", 0.15, 50.0, 18000.0)
        assert r.get("verdicts", {}) == {}
        assert r["mismatches"] == []

    def test_fcf_zero_skips_valuation(self):
        """FCF 为 0 → intrinsic_values 应存在但值为 0"""
        r = _call_from_analyzer("600519", 0.15, 0.0, 18000.0)
        iv = r.get("intrinsic_values", {})
        assert all(v == 0 for v in iv.values())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
