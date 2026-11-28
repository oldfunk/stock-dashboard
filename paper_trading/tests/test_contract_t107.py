"""T107：P2 收尾（fills 表 + 契约探针上屏）。"""
import json
import sqlite3
import threading
import urllib.request
from http.server import HTTPServer

import paper_trading.dashboard as d


def test_contract_payload_wraps_probe(monkeypatch):
    sample = {"ok": True, "available": True, "stale": False, "parts": {
        "strategy": {"ok": True, "asof": "2026-11-23", "stale": False,
                     "shape": {"rows": 20, "parsed": 20}},
        "sector": {"ok": True, "asof": "2026-11-23", "stale": False,
                   "shape": {"coverage": 0.9493}},
        "benchmark": {"ok": True, "asof": "2026-11-23", "stale": False,
                      "shape": {"per_code": {}}}}}
    monkeypatch.setattr("paper_trading.integration.probes.probe_all",
                        lambda mother=None: sample)
    d._CONTRACT_CACHE.update(ts=0.0, data=None)
    assert d._contract_payload(ttl=0) is sample


def test_contract_payload_failclosed(monkeypatch):
    def boom(mother=None):
        raise RuntimeError("probe crashed")

    monkeypatch.setattr("paper_trading.integration.probes.probe_all", boom)
    d._CONTRACT_CACHE.update(ts=0.0, data=None)
    out = d._contract_payload(ttl=0)
    assert out["ok"] is False and out["available"] is False
    assert "RuntimeError" in out["error"]


def test_fills_endpoint_degrades_without_table(tmp_path):
    """旧线/空库没有 fills 表：/api/fills 返回空列表而不是 500。"""
    acct = tmp_path / "acct.db"
    sqlite3.connect(acct).close()

    class _H(d.Handler):
        pass

    _H.data_db = str(tmp_path / "data.db")
    _H.account_db = str(acct)
    _H.secrets_path = str(tmp_path / "s.json")
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{srv.server_port}/api/fills", timeout=10) as r:
            assert r.status == 200
            body = json.loads(r.read().decode())
        assert body["ok"] is True and body["data"] == []
        assert "fills" in body.get("unavailable", "")
    finally:
        srv.shutdown()
        srv.server_close()
