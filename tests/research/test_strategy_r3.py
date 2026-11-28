"""R3-1/2/3：策略登记、版本状态迁移、部署记录。"""
from __future__ import annotations

import pytest

from src.research.domain import DomainError
from src.research.strategy import (BASE_STRATEGY_ID, BASE_SPEC,
                                   create_version, ensure_base_strategy,
                                   ensure_deployment, set_status)


def test_register_base_is_idempotent(minimal_db):
    a = ensure_base_strategy()
    b = ensure_base_strategy()
    assert a["id"] == BASE_STRATEGY_ID == b["id"]
    assert a["spec"]["gates"] and a["spec"]["scoring"]["weights"]["roe"] == 0.30
    assert a["status"] == "paper"


def test_version_requires_supported_hypotheses(minimal_db):
    ensure_base_strategy()
    with pytest.raises(DomainError):
        create_version({"name": "bad"}, hypotheses=["H-20260101-001"])


def test_create_promote_retire(minimal_db):
    ensure_base_strategy()
    v = create_version({"name": "test", "gates": []})
    assert v["status"] == "draft" and v["parent_id"] == BASE_STRATEGY_ID
    assert set_status(v["id"], "paper")["status"] == "paper"
    assert set_status(v["id"], "retired")["status"] == "retired"


def test_ensure_deployment_uses_paper_account(minimal_db, monkeypatch):
    ensure_base_strategy()
    import src.paper_bridge.conn as conn_mod
    import src.paper_bridge.settings as set_mod

    class FakeConn:
        def execute(self, sql, *a):
            if "account" in sql:
                return type("R", (), {"fetchone": lambda s: {
                    "cash": 80000, "initial_cash": 100000}})()
            return type("R", (), {"fetchone": lambda s: {"d": "2026-09-28"}})()

        def close(self):
            pass

    monkeypatch.setattr(set_mod, "bridge_paths", lambda: ("d", "a"))
    monkeypatch.setattr(conn_mod, "connect", lambda p, **k: FakeConn())
    d = ensure_deployment()
    assert d["mode"] == "paper" and d["capital"] == 100000
    assert d["start_date"] == "2026-09-28"
    assert ensure_deployment()["id"] == d["id"]
