"""T116：日/周报（只读；NAV/成交/归因/风控/健康度）。"""
import json

import paper_trading.cli as hb
from paper_trading.report import daily_report, weekly_report, write_report


def _bridge(tmp_path):
    return hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                            account_db=str(tmp_path / "acct.db"),
                            config_path="/nonexistent.yaml",
                            secrets_path=str(tmp_path / "s.json"))


def _seed(b):
    with b.broker._connect() as conn:
        conn.execute("INSERT INTO nav_history(timestamp,cash,market_value,"
                     "total_value,available_cash,pnl,pnl_pct) VALUES"
                     "('2026-11-26T15:00:00',100000,0,100000,100000,0,0)")
        conn.execute("INSERT INTO nav_history(timestamp,cash,market_value,"
                     "total_value,available_cash,pnl,pnl_pct) VALUES"
                     "('2026-11-27T15:00:00',90000,11000,101000,90000,1000,0.01)")
        conn.execute("INSERT INTO fills(order_id,symbol,direction,volume,price,"
                     "commission,stamp_duty,transfer_fee,timestamp) VALUES"
                     "(1,'600519',1,100,100.0,5.0,0,0.01,'2026-11-27T10:00:00')")
        conn.execute("INSERT INTO fills(order_id,symbol,direction,volume,price,"
                     "commission,stamp_duty,transfer_fee,timestamp) VALUES"
                     "(2,'600519',-1,100,110.0,5.0,5.5,0.01,"
                     "'2026-11-27T14:00:00')")
        conn.execute("INSERT INTO op_log(timestamp,action,params,ok,result)"
                     " VALUES('2026-11-27T10:00:00','risk:halt','{}',1,'{}')")
        conn.execute("INSERT INTO op_log(timestamp,action,params,ok,result)"
                     " VALUES('2026-11-27T15:00:00','ai:decide','{}',1,'{}')")
    b.broker.log_operation("ai:config", {"note": "outside-period"}, True,
                           {}, None, None)


def test_daily_report_shape_and_attribution(tmp_path):
    b = _bridge(tmp_path)
    _seed(b)
    out = daily_report(b, "2026-11-27")
    assert out["period"]["start"] == out["period"]["end"] == "2026-11-27"
    assert out["nav"]["change"] == 0.01          # 当日 vs 上一净值日 100000
    assert out["nav"]["previous_day"] == "2026-11-26"
    att = {a["symbol"]: a for a in out["attribution"]}
    rec = att["600519"]
    assert rec["buy_volume"] == 100 and rec["sell_volume"] == 100
    assert rec["buy_amount"] == 10000 and rec["sell_amount"] == 11000
    assert rec["net_cash"] == round(11000 - 10000 - 15.52, 2)
    assert abs(rec["fees"] - 15.52) < 1e-9
    assert out["risk"]["events"][0]["action"] == "risk:halt"
    assert out["operations"]["by_family"]["ai"] == 1
    # 无基准/无健康数据时如实 unavailable，不补零
    assert out["benchmark"]["available"] is False
    assert out["health_change"]["after"]["available"] is False


def test_weekly_report_nav_change_and_health_window(tmp_path):
    b = _bridge(tmp_path)
    _seed(b)
    out = weekly_report(b, "2026-11-27", days=2)
    assert out["period"]["label"] == "weekly(2d)"
    assert out["nav"]["start"] == 100000 and out["nav"]["end"] == 101000
    assert out["nav"]["change"] == 0.01
    assert len(out["trades"]) == 2
    assert out["health_change"]["before"]["available"] is False


def test_daily_benchmark_uses_previous_trading_day(monkeypatch, tmp_path):
    b = _bridge(tmp_path)
    _seed(b)

    def fake_benchmark(codes, days=60, mother=None, ttl=None):
        return {"sh000300": {"available": True, "asof": "2026-11-27",
                             "stale": False,
                             "data": [{"date": "2026-11-26", "close": 100.0},
                                      {"date": "2026-11-27", "close": 99.0}]}}

    monkeypatch.setattr("paper_trading.integration.mother.benchmark",
                        fake_benchmark)
    out = daily_report(b, "2026-11-27")
    assert out["benchmark"]["available"] is True
    assert out["benchmark"]["start"] == "2026-11-26"
    assert out["benchmark"]["return"] == -0.01
    assert round(out["excess_return"], 6) == round(0.01 - (-0.01), 6)


def test_sector_attribution_unknown_and_known(monkeypatch):
    import paper_trading.report as r

    def fake_sectors(symbols):
        return {"available": True, "source": "mother:stock_snapshot",
                "asof": "2026-11-27", "stale": False,
                "data": {"600519": "食品饮料"}}

    monkeypatch.setattr("paper_trading.integration.mother.sectors",
                        fake_sectors)
    out = r._sector_attribution([
        {"symbol": "600519", "market_value": 30000},
        {"symbol": "000001", "market_value": 10000},
    ])
    assert out["available"] is True
    assert out["unknown"] == 1 and out["known"] == 1
    items = {x["sector"]: x for x in out["items"]}
    assert items["食品饮料"]["weight"] == 0.75
    assert items["未知"]["weight"] == 0.25


def test_sector_attribution_unavailable(monkeypatch):
    import paper_trading.report as r

    monkeypatch.setattr("paper_trading.integration.mother.sectors",
                        lambda syms: {"available": False, "note": "no"})
    out = r._sector_attribution([{"symbol": "600519", "market_value": 1}])
    assert out["available"] is False and out["reason"] == "no"


def test_report_write_json(tmp_path):
    b = _bridge(tmp_path)
    _seed(b)
    out = daily_report(b, "2026-11-27")
    p = write_report(out, tmp_path / "reports" / "d.json")
    data = json.loads(open(p, encoding="utf-8").read())
    assert data["period"]["label"] == "daily"
    assert data["attribution"][0]["symbol"] == "600519"
