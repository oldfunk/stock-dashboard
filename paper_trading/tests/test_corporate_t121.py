"""T121：分红税口径 + 除权除息建议（B4/B5，只建议不落账）。"""
from types import SimpleNamespace

import paper_trading.cli as hb
from paper_trading.utils import corporate_actions as ca


def test_tax_rate_boundaries():
    assert ca.tax_rate(0) == 0.20
    assert ca.tax_rate(30) == 0.20
    assert ca.tax_rate(31) == 0.10
    assert ca.tax_rate(365) == 0.10
    assert ca.tax_rate(366) == 0.0
    assert ca.tax_rate(None) is None
    assert ca.tax_rate(-1) is None


def test_estimate_tax_per_position():
    pos = [SimpleNamespace(symbol="600519", total_volume=100,
                           entry_day="2026-10-20")]   # 到 11-27 = 38 天 → 10%
    out = ca.estimate_tax(pos, 5.0, "2026-11-27")
    item = out["items"][0]
    assert item["holding_days"] == 38
    assert item["tax_rate"] == 0.10
    assert item["gross_dividend"] == 500.0
    assert item["tax"] == 50.0 and item["net"] == 450.0
    assert out["complete"] is True


def test_estimate_tax_missing_entry_day_is_not_zero():
    pos = [SimpleNamespace(symbol="600519", total_volume=100, entry_day="")]
    out = ca.estimate_tax(pos, 5.0, "2026-11-27")
    item = out["items"][0]
    assert item["tax_rate"] is None and item["tax"] is None
    assert item["needs_manual_holding_period"]
    assert out["complete"] is False


def test_suggest_mother_event_needs_manual_confirm(monkeypatch):
    pos = [SimpleNamespace(symbol="600519", total_volume=100,
                           entry_day="2020-01-01")]
    monkeypatch.setattr(ca, "_from_mother", lambda syms: [{
        "symbol": "600519", "cash_per_share": 2.0, "share_ratio": 0.0,
        "report_date": "2025-12-31", "ex_date": None,
        "source": "mother:dividend_annual", "asof": "2026-01-01"}])
    out = ca.suggest(pos)
    s = out["suggestions"][0]
    assert s["status"] == "needs_manual_confirm"
    assert s["needs_ex_date"]
    assert "command" not in s


def test_suggest_with_ex_date_builds_tax_and_command(monkeypatch):
    pos = [SimpleNamespace(symbol="600519", total_volume=100,
                           entry_day="2020-01-01")]
    monkeypatch.setattr(ca, "_from_akshare", lambda syms: [{
        "symbol": "600519", "cash_per_share": 2.0, "share_ratio": 0.0,
        "report_date": None, "ex_date": "2026-11-27",
        "source": "akshare:test", "asof": None}])
    out = ca.suggest(pos, source="akshare")
    s = out["suggestions"][0]
    assert s["tax_estimate"]["items"][0]["tax_rate"] == 0.0
    assert "corporate apply" in s["command"]
    assert "--ex-date 2026-11-27" in s["command"]


def test_suggest_does_not_write_ledger(tmp_path):
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    cash0 = b.broker.get_cash()
    out = ca.suggest(b.broker.get_all_positions())
    assert out["ok"] is True and out["suggestions"] == []
    assert b.broker.get_cash() == cash0
