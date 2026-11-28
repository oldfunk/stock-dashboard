"""T119：逐日审计串起计划/决策/成交/条件单（B2）。"""
import paper_trading.cli as hb
from paper_trading.audit_day import audit_day
from paper_trading.engine.conditional import ConditionalOrders


def _bridge(tmp_path):
    return hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                            account_db=str(tmp_path / "acct.db"),
                            config_path="/nonexistent.yaml",
                            secrets_path=str(tmp_path / "s.json"))


def test_audit_day_links_plan_decision_fill(tmp_path):
    b = _bridge(tmp_path)
    ConditionalOrders(b.account_db_path)          # 建条件单表
    pid = b.broker.save_plan("2026-11-27", ["600519"], {
        "actions": [
            {"action": "buy", "symbol": "600519", "volume": 100,
             "price": 100.0, "reason": "趋势向上", "strategy": "ma_cross"},
            {"action": "hold", "symbol": "000858", "volume": 0,
             "reason": "等回调"},
        ],
        "actions_rejected": [
            {"action": "buy", "symbol": "601318", "reason": "想买",
             "reject_reason": "资金分配：超策略额度"},
        ],
        "summary": "健康降级，放缓买入",
        "strategy_health": {"health": "demote", "recommendation": "observe"},
        "strategy_allocations": {"enabled": True},
    })
    b.broker.save_agent_decision(
        decision_date="2026-11-27", plan_id=pid, mode="live", from_plan=True,
        summary="健康降级，放缓买入",
        actions=[
            {"action": "buy", "symbol": "600519", "volume": 100,
             "reason": "趋势向上", "status": "已成交 @100.00",
             "strategy": "ma_cross"},
            {"action": "hold", "symbol": "000858", "reason": "等回调",
             "status": "持有不动"},
        ],
        conditions=[], result={"strategy_health": {"health": "demote"}})
    with b.broker._connect() as conn:
        conn.execute("INSERT INTO fills(order_id,symbol,direction,volume,price,"
                     "commission,stamp_duty,transfer_fee,timestamp) VALUES"
                     "(1,'600519',1,100,100.0,5.0,0,0.01,'2026-11-27T10:00:00')")
        conn.execute("INSERT INTO conditional_orders(created_at,day,source,"
                     "symbol,side,cond_type,cond_value,volume,value,expiry,"
                     "status,note) VALUES('2026-11-27T09:00:00','2026-11-27',"
                     "'ai','000858','buy','price_below',90.0,100,0,"
                     "'2026-11-30T15:00:00','armed','等回调')")
        conn.execute("CREATE TABLE IF NOT EXISTS rq_intents("
                     "biz_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,"
                     "side TEXT NOT NULL, volume INTEGER NOT NULL,"
                     "rule_ref TEXT, detail TEXT,"
                     "status TEXT NOT NULL DEFAULT 'pending',"
                     "created_at TEXT NOT NULL, result TEXT)")
        conn.execute("INSERT INTO rq_intents(biz_id,symbol,side,volume,status,"
                     "created_at) VALUES('600519_BUY_100','600519','BUY',100,"
                     "'executed','2026-11-27T09:31:00')")

    out = audit_day(b, "2026-11-27")
    assert out["ok"] is True
    assert out["counts"]["plans"] == 1
    assert out["counts"]["decisions"] == 1
    assert out["counts"]["intents"] == 1
    syms = {t["symbol"]: t for t in out["timeline"]}
    buy = syms["600519"]
    assert buy["outcome"] == "有买入成交"
    assert buy["intents"][0]["status"] == "executed"
    assert any(w["exec_status"].startswith("已成交") for w in buy["why"])
    assert buy["plan_health"]["health"] == "demote"
    rej = syms["601318"]
    assert any("额度" in str(w.get("reason")) for w in rej["why"])
    assert rej["outcome"] == "当日无成交"
    assert rej["evidence_gap"]
    cond = syms["000858"]
    assert cond["conditions"][0]["status"] == "armed"
    assert cond["outcome"] == "当日无成交"


def test_audit_day_empty_is_not_error(tmp_path):
    b = _bridge(tmp_path)
    out = audit_day(b, "2026-11-20")
    assert out["ok"] is True
    assert out["counts"]["plans"] == 0 and out["timeline"] == []
