"""P2-2（批8）：基准指数代码规范化与"取到 0 行"不再静默（2026-10-29）。

旧实现 `refresh_benchmarks` 里 `targets[b["code"]] = b["code"]` 直接拿 config
字符串当存储键与拉取符号。而 `refresh_outcomes` 也用同一个 `_benchmarks()`
的 `b["code"]` 写 `analysis_outcome.benchmark_code`——所以规范化必须做在
`_benchmarks()` 这个**共用入口**上，否则写库侧与读数侧会分叉。

注：子项目契约标准码（000001/399001/399006，Issue #5）有意存**裸码**，由
`contract_sources` 单独并入，不经 `_benchmarks`，本件不动它（生产实测键位：
000001/399001/399006 与 sh000300/sh000922 并存）。
"""

import pytest

from src.outcome_service import _benchmarks, normalize_index_code


class TestNormalize:
    @pytest.mark.parametrize("raw,expect", [
        ("sh000300", "sh000300"),      # 已规范：幂等
        ("SH000922", "sh000922"),      # 大小写
        (" sz399001 ", "sz399001"),    # 前后空白
        ("000300", "sh000300"),        # 裸码推断为沪市
        ("399001", "sz399001"),        # 深市指数段
        ("159915", "sz159915"),        # 深市基金段
    ])
    def test_accepts_and_normalizes(self, raw, expect):
        assert normalize_index_code(raw) == expect

    @pytest.mark.parametrize("bad", ["", None, "abc", "00030", "sh00030",
                                     "csi000300", "沪深300"])
    def test_rejects_unrecognizable(self, bad):
        with pytest.raises(ValueError):
            normalize_index_code(bad)

    def test_idempotent_on_second_pass(self):
        once = normalize_index_code("000300")
        assert normalize_index_code(once) == once


class TestBenchmarksEntry:
    def test_config_bare_code_is_canonicalized(self):
        """写库侧与读数侧共用 _benchmarks → 规范化在这里做才不会分叉。"""
        cfg = {"outcomes": {"benchmarks": [{"code": "000300", "name": "沪深300"}]}}
        got = _benchmarks(cfg)
        assert got == [{"code": "sh000300", "name": "沪深300"}]
        assert normalize_index_code(got[0]["code"]) == got[0]["code"]

    def test_prefixed_config_unchanged(self):
        """生产现配置（DEFAULT 与 config.yaml 都是带前缀）行为不变。"""
        cfg = {"outcomes": {"benchmarks": [{"code": "sh000300",
                                            "name": "沪深300"},
                                           {"code": "sh000922",
                                            "name": "中证红利"}]}}
        assert [b["code"] for b in _benchmarks(cfg)] == ["sh000300", "sh000922"]

    def test_bad_config_raises_not_silently_kept(self):
        with pytest.raises(ValueError):
            _benchmarks({"outcomes": {"benchmarks": [{"code": "沪深300"}]}})

    def test_empty_config_falls_back_to_defaults(self):
        from src.analyzer.outcomes import DEFAULT_BENCHMARKS
        assert _benchmarks({}) == DEFAULT_BENCHMARKS


class TestRefreshBenchmarks:
    @pytest.fixture
    def stubs(self, monkeypatch):
        """打桩两个取数函数与 DAO。

        约定：东财对**深证红利一类以 922 结尾的代码**返回空，用来走"两源全空"
        分支（真实世界里 sh000922 就是新浪侧滞后的那一个）。
        """
        import src.collector.akshare_fetcher as akf
        import src.models.database as db
        seen = {"fetch": [], "save": []}

        def em(symbol, limit=6000):
            seen["fetch"].append(symbol)
            if symbol.endswith("922"):
                return []
            return [{"trade_date": "2026-10-28", "close": 4000.0}]

        def sina(symbol, limit=6000):
            seen["fetch"].append("sina:" + symbol)
            return []                      # 回退也空 → 两源皆空

        class FakeDAO:
            def upsert_many(self, code, records):
                seen["save"].append((code, len(records)))
                return len(records)

        monkeypatch.setattr(akf, "fetch_index_kline_em", em)
        monkeypatch.setattr(akf, "fetch_index_kline", sina)
        monkeypatch.setattr(db, "BenchmarkDailyDAO", FakeDAO)
        return seen

    def test_bare_config_reaches_dao_as_canonical_key(self, stubs):
        from src.outcome_service import refresh_benchmarks
        stats = refresh_benchmarks(
            {"outcomes": {"benchmarks": [{"code": "000300"}]}})
        assert ("sh000300", 1) in stubs["save"], \
            f"落库键必须与读数侧一致，实际 {stubs['save']}"
        assert stats["sh000300"] == 1

    def test_zero_rows_warns_loudly(self, stubs, caplog):
        """非空校验：0 行不再是"看起来一切正常"的一条 info。"""
        import logging
        from src.outcome_service import refresh_benchmarks
        with caplog.at_level(logging.WARNING, logger="src.outcome_service"):
            stats = refresh_benchmarks(
                {"outcomes": {"benchmarks": [{"code": "sh000922"}]}})
        assert stats["sh000922"] == 0
        msgs = [r.getMessage() for r in caplog.records
                if "取到 0 行" in r.getMessage()]
        assert msgs, f"空结果必须留下可定位的告警，实际：{caplog.text!r}"
        assert "sh000922" in msgs[0]
        # 两源都试过（回退链没被短路）
        assert "sh000922" in stubs["fetch"] and "sina:sh000922" in stubs["fetch"]
