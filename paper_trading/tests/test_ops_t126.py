"""T126：运维巡检（自动备份轮转/新鲜度 + ops check 聚合）。"""
from datetime import datetime, timedelta
from pathlib import Path

import paper_trading.cli as hb
from paper_trading.utils import ledger_backup as lb
from paper_trading.utils import ops_status as ops


def _bridge(tmp_path):
    return hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                            account_db=str(tmp_path / "acct.db"),
                            config_path="/nonexistent.yaml",
                            secrets_path=str(tmp_path / "s.json"))


def test_backup_keep_prunes_only_primary(tmp_path):
    b = _bridge(tmp_path)
    bk = tmp_path / "bk"
    for stamp in ("20260101T000000", "20260102T000000", "20260103T000000"):
        out = lb.backup(b.broker.db_path, out_dir=bk, stamp=stamp, keep=2)
        assert out["ok"] is True
    names = sorted(p.name for p in bk.glob("acct.*.db"))
    assert names == ["acct.20260102T000000.db", "acct.20260103T000000.db"]

    # 安全副本/演练副本不参与轮转
    safety = bk / "acct.pre-restore.20260103T000000.db"
    safety.write_bytes(b"x")
    drill = bk / "acct.drill.db"
    drill.write_bytes(b"x")
    out = lb.backup(b.broker.db_path, out_dir=bk, stamp="20260104T000000", keep=1)
    names = sorted(p.name for p in bk.glob("acct.*.db"))
    assert "acct.20260104T000000.db" in names
    assert safety.name in names and drill.name in names


def test_backup_status_freshness_and_missing(tmp_path):
    b = _bridge(tmp_path)
    bk = tmp_path / "bk"
    miss = lb.backup_status(b.broker.db_path, out_dir=bk)
    assert miss["ok"] is False and miss["reason"] == "备份目录不存在"

    lb.backup(b.broker.db_path, out_dir=bk, stamp="20260101T000000")
    st = lb.backup_status(b.broker.db_path, out_dir=bk, max_age_hours=26)
    assert st["ok"] is True and st["count"] == 1
    assert st["integrity"] == "ok"
    stale = lb.backup_status(b.broker.db_path, out_dir=bk, max_age_hours=26,
                             now=datetime.now() + timedelta(hours=30))
    assert stale["ok"] is False and "未更新" in stale["reason"]


def test_ops_check_red_without_backup_then_green(tmp_path):
    b = _bridge(tmp_path)
    out = ops.build_status(b, backup_dir=tmp_path / "bk",
                           max_backup_age_hours=26)
    assert out["ok"] is False
    assert "backup" in out["red"]
    assert "backup" in [c["id"] for c in out["checks"]]

    lb.backup(b.broker.db_path, out_dir=tmp_path / "bk", stamp="20260101T000000")
    out2 = ops.build_status(b, backup_dir=tmp_path / "bk",
                            max_backup_age_hours=26)
    assert out2["ok"] is True
    ids = {c["id"]: c for c in out2["checks"]}
    assert ids["backup"]["status"] == "green"
    assert ids["risk"]["status"] == "green"

    p = ops.write_status(out2, tmp_path / "ops.json")
    assert Path(p).exists()
    assert ops.read_status(tmp_path / "ops.json")["ok"] is True
