"""子项目 :8081 面板只读客户端：正常归一化与失败降级。"""
from __future__ import annotations

import json

from src.paper_bridge import panel


class _Resp:
    status = 200

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_panel_snapshot_normalizes(monkeypatch):
    perf = {"summary": {
        "total_return": -0.0287, "total_pnl": -2874.68,
        "total_value": 97125.32, "cash": 82399.32, "market_value": 14726.0,
        "today_pnl": 811.99, "today_pnl_pct": 0.0084, "mdd": 0.0417,
        "realized_pnl": -2859.54, "fees_total": 40.68, "closed_trades": 2,
        "win_rate": 0.0, "exposure": 0.1516, "sharpe": -5.8,
        "mdd_date": "2026-11-20", "current_drawdown": 0.029,
        "win_days": 6, "lose_days": 11, "vol_annual": 0.07,
        "win_trades": 0, "lose_trades": 2, "estimated": False}}
    overview = {"date": "2026-11-27", "estimated": False,
                "status": {"data_asof": "2026-11-27"}}
    positions = [{"symbol": "000792", "total_volume": 400,
                  "available_volume": 400, "avg_cost": 23.98,
                  "current_price": 22.64, "market_value": 9056.0,
                  "entry_day": "2026-10-08", "price_asof": "2026-11-27",
                  "spark": [1, 2]}]
    calls = []

    def fake_urlopen(req, timeout=2.0):
        calls.append(req.full_url)
        path = req.full_url.split("8081", 1)[1]
        return _Resp({"ok": True, "data": {
            "/api/performance": perf,
            "/api/overview": overview,
            "/api/positions": positions,
        }[path]})

    monkeypatch.setattr(panel.urllib.request, "urlopen", fake_urlopen)
    snap = panel.load_panel_snapshot()
    assert snap["available"] is True
    assert snap["asof"] == "2026-11-27"
    assert snap["performance"]["total_return"] == -0.0287
    assert snap["performance"]["realized_pnl"] == -2859.54
    assert snap["today"]["today_pnl"] == 811.99
    assert snap["positions"][0]["symbol"] == "000792"
    assert "spark" not in snap["positions"][0]      # 控制 prompt 体积
    assert len(calls) == 3


def test_panel_failure_returns_unavailable(monkeypatch):
    def boom(req, timeout=2.0):
        raise OSError("connection refused")

    monkeypatch.setattr(panel.urllib.request, "urlopen", boom)
    snap = panel.load_panel_snapshot()
    assert snap["available"] is False
    assert snap["source"] == "paper:panel"
