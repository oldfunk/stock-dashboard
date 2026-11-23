"""T96：计划/决策的新鲜度画像（不许再用 max(asof) 掩盖个股陈旧）。"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent.loop import FRESH_COVERAGE_MIN, freshness_of  # noqa: E402


def _bars(dates):
    return [SimpleNamespace(timestamp=datetime.fromisoformat(d + "T15:00:00"))
            for d in dates]


def test_all_fresh_coverage_one():
    all_bars = {s: _bars(["2026-11-20"]) for s in ("000001", "000002", "600000")}
    f = freshness_of(all_bars, list(all_bars), ["000001"],
                     "2026-11-20")
    assert f["fresh"] == 3 and f["total"] == 3
    assert f["coverage"] == 1.0
    assert f["stale"] == {} and f["holdings_stale"] == {}
    assert f["oldest"] == "2026-11-20"


def test_max_hides_stale_but_coverage_catches_it():
    """1 只当日、2 只停在 11-13：旧 max(asof) 会放行，覆盖率只有 1/3。"""
    all_bars = {
        "000001": _bars(["2026-11-20"]),
        "000002": _bars(["2026-11-13"]),
        "600000": _bars(["2026-11-13"]),
    }
    f = freshness_of(all_bars, list(all_bars), [], "2026-11-20")
    assert f["fresh"] == 1 and f["coverage"] == 0.3333
    assert f["coverage"] < FRESH_COVERAGE_MIN
    assert f["stale"] == {"000002": "2026-11-13", "600000": "2026-11-13"}
    assert f["oldest"] == "2026-11-13"


def test_holdings_stale_is_called_out_even_if_coverage_passes():
    """覆盖率可以给候选池留容错；持仓没有容错（卖不掉/定不了价）。"""
    all_bars = {f"00000{i}": _bars(["2026-11-20"]) for i in range(5)}
    all_bars["600000"] = _bars(["2026-11-13"])       # 持仓里的陈旧项
    symbols = list(all_bars)
    f = freshness_of(all_bars, symbols, ["600000"], "2026-11-20")
    assert f["coverage"] == 0.8333 and f["coverage"] >= FRESH_COVERAGE_MIN
    assert f["holdings_stale"] == {"600000": "2026-11-13"}


def test_missing_bars_count_as_stale():
    all_bars = {"000001": _bars(["2026-11-20"]), "000002": []}
    f = freshness_of(all_bars, ["000001", "000002"], [], "2026-11-20")
    assert f["fresh"] == 1 and f["stale"]["000002"] is None
    assert f["coverage"] == 0.5


def test_empty_pool_is_trivially_fresh():
    f = freshness_of({}, [], [], "2026-11-20")
    assert f["coverage"] == 1.0 and f["total"] == 0 and f["oldest"] is None
