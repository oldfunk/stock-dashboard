"""执行侧行情数据源：母库优先/本地兜底（逐标的降级）。"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paper_trading.cli as hb  # noqa: E402
from paper_trading.data.source import BarSource  # noqa: E402
from paper_trading.models import Bar  # noqa: E402


def _mk_local(tmp):
    b = hb.TradingBridge(data_db=str(tmp / "d.db"), account_db=str(tmp / "a.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp / "s.json"))
    # 真实量级：与合成母库（600519≈1495/1505）同日同价（差 <0.01%）
    bars = [Bar(symbol="600519", timestamp=datetime(2026, 10, 20) + timedelta(days=i),
                open=1490, high=1500, low=1485, close=1490 + i, volume=100)
            for i in range(5)]
    b.data_db.upsert_bars(bars)
    return b


def test_local_source_uses_local(tmp_path):
    b = _mk_local(tmp_path)
    src = BarSource(b.data_db, source="local")
    bars = src.get_bars("600519", limit=3)
    assert len(bars) == 3 and bars[0].close == 1492.0   # 取最近 3 根（升序）
    assert src.source_of("600519") == "local:akshare"


def test_auto_falls_back_to_local_without_mother(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(tmp_path / "no-sd"))
    b = _mk_local(tmp_path)
    src = BarSource(b.data_db, source="auto")
    assert len(src.get_bars("600519", limit=3)) == 3
    assert src.source_of("600519") == "local:akshare"


def test_auto_prefers_mother_when_present(tmp_path, monkeypatch):
    import test_mother_contract as t

    sd = t._mk_sd(tmp_path)
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(sd))
    b = _mk_local(tmp_path)
    src = BarSource(b.data_db, source="auto")
    bars = src.get_bars("600519", limit=3)
    assert len(bars) == 2
    assert bars[0].close == 1495.0 and bars[1].close == 1505.0   # 升序
    assert src.source_of("600519") == "mother:kline_daily"


def test_bridge_get_bars_delegates(tmp_path):
    b = _mk_local(tmp_path)
    assert len(b.get_bars("600519", limit=2)) == 2
    assert b.daily_source in ("auto", "local", "mother")


def test_mother_only_mode_no_local_fallback(tmp_path, monkeypatch):
    """M-1：data.daily_source=mother 是真开关——缺母库不回退本地（fail-closed）。"""
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(tmp_path / "no-sd"))
    b = _mk_local(tmp_path)          # 本地有 600519 数据
    src = BarSource(b.data_db, source="mother")
    assert src.get_bars("600519", limit=3) == [], \
        "mother 模式缺母库必须返回空（旧行为静默回退本地=死配置）"
    assert src.source_of("600519") == "unavailable"


def test_mother_only_mode_uses_mother(tmp_path, monkeypatch):
    """M-1：mother 模式有母库时只读母库（无视本地）。"""
    import test_mother_contract as t

    sd = t._mk_sd(tmp_path)
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(sd))
    b = _mk_local(tmp_path)
    src = BarSource(b.data_db, source="mother")
    bars = src.get_bars("600519", limit=3)
    assert bars and bars[-1].close == 1505.0      # 母库价，非本地 1494
    assert src.source_of("600519") == "mother:kline_daily"
