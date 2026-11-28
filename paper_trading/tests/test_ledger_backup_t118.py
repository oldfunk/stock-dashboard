"""T118：账本备份/恢复演练/版本化（B1）。"""
import sqlite3

import paper_trading.cli as hb
from paper_trading.utils import ledger_backup as lb


def _bridge(tmp_path):
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    with b.broker._connect() as conn:
        conn.execute("INSERT INTO nav_history(timestamp,cash,market_value,"
                     "total_value,available_cash,pnl,pnl_pct) VALUES"
                     "('2026-11-27T15:00:00',100000,0,100000,100000,0,0)")
    return b


def test_backup_and_inspect(tmp_path):
    b = _bridge(tmp_path)
    out = lb.backup(b.broker.db_path, out_dir=tmp_path / "bk", stamp="t")
    assert out["ok"] is True
    assert out["row_counts"]["nav_history"] == 1
    insp = lb.inspect(out["backup"])
    assert insp["ok"] is True and insp["integrity"] == "ok"
    assert insp["row_counts"]["account"] == 1


def test_restore_dry_run_then_apply(tmp_path):
    b = _bridge(tmp_path)
    bk = lb.backup(b.broker.db_path, out_dir=tmp_path / "bk", stamp="t")
    # 目标新增一行，恢复后应回到备份时点
    with b.broker._connect() as conn:
        conn.execute("INSERT INTO nav_history(timestamp,cash,market_value,"
                     "total_value,available_cash,pnl,pnl_pct) VALUES"
                     "('2026-11-28T15:00:00',100000,0,100000,100000,0,0)")
    dry = lb.restore(bk["backup"], b.broker.db_path)
    assert dry["ok"] is True and dry["applied"] is False
    assert lb.inspect(b.broker.db_path)["row_counts"]["nav_history"] == 2

    out = lb.restore(bk["backup"], b.broker.db_path, apply=True,
                     out_dir=tmp_path / "safety")
    assert out["ok"] is True and out["applied"] is True
    assert out["safety_backup"]
    insp = lb.inspect(b.broker.db_path)
    assert insp["row_counts"]["nav_history"] == 1
    # 恢复后留痕
    assert insp["row_counts"]["op_log"] >= 1


def test_drill_on_copy(tmp_path):
    b = _bridge(tmp_path)
    out = lb.drill(b.broker.db_path, tmp_path / "drill")
    assert out["ok"] is True
    assert out["row_counts_restored"]["nav_history"] == 1
    # 生产账本未被 drill 改动
    assert lb.inspect(b.broker.db_path)["row_counts"]["nav_history"] == 1


def test_migrate_versioning(tmp_path):
    b = _bridge(tmp_path)
    dry = lb.migrate(b.broker.db_path)
    assert dry["ok"] is True and dry["applied"] is False
    assert any("schema" in s for s in dry["steps"])

    out = lb.migrate(b.broker.db_path, apply=True)
    assert out["ok"] is True and out["applied"] is True
    assert out["inspect_after"]["schema_version"] == lb.ACCOUNT_SCHEMA_VERSION
    again = lb.migrate(b.broker.db_path)
    assert again["ok"] is True and again.get("note", "").startswith("已是最新")


def test_migrate_rejects_future_version(tmp_path):
    b = _bridge(tmp_path)
    with b.broker._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_meta"
                     "(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)")
        conn.execute("INSERT INTO schema_meta(key,value) VALUES"
                     "('schema_version','999')")
    out = lb.migrate(b.broker.db_path)
    assert out["ok"] is False and "高于" in out["error"]
