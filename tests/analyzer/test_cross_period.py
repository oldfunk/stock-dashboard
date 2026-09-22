"""复盘跨期对照单测（分析师 critique #8：上期 vs 本期矛盾显式标注）。

覆盖 _check_cross_period 纯函数：
- vanished / unrecorded / signal_flip 三种 findings
- 未知 signal 不判翻转；空上期不判存量问题；坏输入不抛异常
"""

from src.analyzer.watchlist_reviewer import _check_cross_period


def _details(**kw):
    base = {"add": [], "keep": [], "watch": [], "remove": []}
    base.update(kw)
    return base


def test_signal_flip():
    prev = _details(keep=[{"code": "600519"}])
    cur = ["600519"]
    sigs = {"600519": ("BUY", "AVOID")}
    out = _check_cross_period(prev, cur, sigs)
    assert len(out) == 1
    assert out[0]["type"] == "signal_flip"
    assert "BUY" in out[0]["message"] and "AVOID" in out[0]["message"]


def test_no_flip_when_unknown_or_same():
    prev = _details(keep=[{"code": "A"}, {"code": "B"}, {"code": "C"}])
    cur = ["A", "B", "C"]
    sigs = {"A": ("BUY", "BUY"), "B": (None, "AVOID"), "C": ("HOLD", None)}
    assert _check_cross_period(prev, cur, sigs) == []


def test_vanished_and_unrecorded():
    prev = _details(keep=[{"code": "OLD"}], add=[{"code": "KEEP"}])
    cur = ["KEEP", "NEW"]
    out = _check_cross_period(prev, cur, {})
    types = {f["type"] for f in out}
    assert types == {"vanished", "unrecorded"}
    assert any("OLD" in f["message"] for f in out if f["type"] == "vanished")
    assert any("NEW" in f["message"] for f in out if f["type"] == "unrecorded")


def test_empty_prev_means_first_run():
    assert _check_cross_period({}, ["A"], {}) == []
    assert _check_cross_period(None, ["A"], None) == []


def test_bad_inputs_never_raise():
    assert _check_cross_period("nope", None, None) == []
    assert _check_cross_period({"add": [{"no": 1}, "x"]}, ["A"],
                               {"A": ("BUY",)}) == []


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
