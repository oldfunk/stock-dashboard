"""面板「规则引擎 + 条件单」接口单测（本地 HTTP，全程离线）。"""
import json
import socket
import sys
import threading
import urllib.request
from datetime import datetime, timedelta
from http.server import HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.models import Bar  # noqa: E402


@pytest.fixture()
def panel(tmp_path):
    """起一个真面板实例（127.0.0.1 随机端口），返回 (base_url, dbs)。"""
    import paper_trading.dashboard as d
    from paper_trading.cli import TradingBridge
    from paper_trading.llm.secrets import save_provider

    data_db = str(tmp_path / "data.db")
    acct_db = str(tmp_path / "acct.db")
    secrets = save_provider({"preset": "custom", "base_url": "https://x.test/v1",
                             "model": "m", "api_key": "sk-fake-key"},
                            path=str(tmp_path / "s.json"))
    token = json.loads(secrets.read_text(encoding="utf-8"))["admin_token"]

    b = TradingBridge(data_db=data_db, account_db=acct_db,
                      config_path="/nonexistent.yaml",
                      secrets_path=str(tmp_path / "s.json"))
    days = [(datetime(2026, 1, 1) + timedelta(days=i)) for i in range(90)]
    # 先平 40 根再涨：让均线真的发生一次金叉（线性上涨永远不交叉）
    closes = [10.0 if i < 40 else 10.0 + (i - 40) * 0.2 for i in range(90)]
    b.data_db.upsert_bars([Bar(symbol="000001", timestamp=d, open=c,
                               high=c * 1.02, low=c * 0.98, close=c,
                               volume=1000) for d, c in zip(days, closes)])

    class _H(d.Handler):
        pass

    _H.data_db = data_db
    _H.account_db = acct_db
    _H.secrets_path = str(tmp_path / "s.json")
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    yield base, {"token": token, "data_db": data_db, "acct_db": acct_db}
    srv.shutdown()
    srv.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def _post(url, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode()), e.code


def test_engine_view_is_public(panel):
    base, _ = panel
    r = _get(f"{base}/api/engine")
    assert r["ok"] and "rules" in r["data"] and "valid" in r["data"]
    assert isinstance(r["data"]["triggers"], list)


def test_engine_backtest_requires_token(panel):
    base, dbs = panel
    r, code = _post(f"{base}/api/engine/backtest", {"days": 30})
    assert code == 403 and r["ok"] is False


def test_engine_backtest_and_optimize_with_token(panel):
    base, dbs = panel
    r, code = _post(f"{base}/api/engine/backtest",
                    {"admin_token": dbs["token"], "days": 90, "symbols": ["000001"],
                     "cash": 100000, "curve": True})
    assert code == 200, r
    d = r["data"]
    assert "total_return_pct" in d and "benchmark_buy_hold" in d
    assert "excess_pct" in d and d["days"] == 90
    assert d.get("ascii_curve")

    r2, code2 = _post(f"{base}/api/engine/optimize",
                      {"admin_token": dbs["token"], "grid": "ma_cross",
                       "symbols": ["000001"], "days": 90, "top": 3})
    assert code2 == 200, r2
    assert r2["data"]["variants"] > 0 and len(r2["data"]["top"]) <= 3
    assert all(t["trades"] >= 1 for t in r2["data"]["top"])


def test_engine_optimize_unknown_grid(panel):
    base, dbs = panel
    r, code = _post(f"{base}/api/engine/optimize",
                    {"admin_token": dbs["token"], "grid": "nope"})
    assert code == 400 and "未知网格" in r["error"]


def test_engine_walkforward_endpoint(panel):
    base, dbs = panel
    r, code = _post(f"{base}/api/engine/walkforward",
                    {"admin_token": dbs["token"], "grid": "ma_cross",
                     "symbols": ["000001"], "days": 90, "split": 0.6, "top": 3})
    assert code == 200, r
    d = r["data"]
    assert "split" in d and "out_of_sample" in d
    sp = d["split"]
    assert sp["in_days"] + sp["oos_days"] == 90
    assert sp["in_sample"][1] < sp["out_of_sample"][0]


def test_engine_walkforward_requires_token(panel):
    base, _ = panel
    r, code = _post(f"{base}/api/engine/walkforward", {"days": 90})
    assert code == 403 and r["ok"] is False


def test_cond_crud_flow(panel):
    base, dbs = panel
    t = dbs["token"]
    assert _get(f"{base}/api/cond")["data"]["orders"] == []

    r, code = _post(f"{base}/api/cond", {
        "admin_token": t, "symbol": "000001", "side": "buy",
        "cond_type": "price_below", "cond_value": 9.5, "value": 20000,
        "note": "回踩支撑"})
    assert code == 200 and r["data"]["id"] == 1, r
    d = _get(f"{base}/api/cond")["data"]
    assert d["armed"] == 1 and d["orders"][0]["status"] == "armed"
    assert d["orders"][0]["note"] == "回踩支撑"

    # 非法条件单：给真实原因，不静默
    r2, code2 = _post(f"{base}/api/cond", {
        "admin_token": t, "symbol": "510300", "side": "buy",
        "cond_type": "price_below", "cond_value": 9.5, "value": 20000})
    assert code2 == 400 and "非 A 股" in r2["error"]

    r3, code3 = _post(f"{base}/api/cond/cancel", {"admin_token": t, "id": 1})
    assert code3 == 200 and r3["data"]["cancelled"] is True
    assert _get(f"{base}/api/cond")["data"]["armed"] == 0
    r4, code4 = _post(f"{base}/api/cond/cancel", {"admin_token": t, "id": 1})
    assert code4 == 400 and "已成交" in r4["error"]


def test_cond_rejects_below_min_ticket(panel):
    """手续费地板：入库前就拒掉过小的买入金额，不给"挂得上、永远触发不了"的单子。"""
    base, dbs = panel
    r, code = _post(f"{base}/api/cond", {
        "admin_token": dbs["token"], "symbol": "000001", "side": "buy",
        "cond_type": "price_below", "cond_value": 9.5, "value": 100})
    assert code == 400 and "最小下单金额" in r["error"]
    assert _get(f"{base}/api/cond")["data"]["orders"] == []


def test_cond_requires_token(panel):
    base, _ = panel
    r, code = _post(f"{base}/api/cond", {
        "symbol": "000001", "side": "buy", "cond_type": "price_below",
        "cond_value": 9.5, "value": 20000})
    assert code == 403 and r["ok"] is False


def test_page_has_engine_and_cond_ui(panel):
    import paper_trading.dashboard as d

    page = d.PAGE
    for need in ("规则引擎", "条件单", "/api/engine/backtest", "/api/engine/optimize",
                 "/api/engine/walkforward", "/api/cond/cancel", "/api/cond/design",
                 "btn-cond-add", "btn-engine-wf", "engine-trig", "conds"):
        assert need in page, f"面板缺少：{need}"
    # 面板不得出现英文系统词
    for bad in ("Cancel", "Delete", "Submit", "Refresh", "Loading..."):
        assert bad not in page, f"面板出现英文系统词：{bad}"
