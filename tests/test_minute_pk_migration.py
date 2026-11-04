"""T35 钱类数据层迁移：`minute_bars` 主键补 `period`（§5.5 裁决 6）。

覆盖"迁移正确性"与"覆盖风险本身"两件事：
迁移前 1 分钟与 5 分钟在同一时刻会互相覆盖（旧主键 `(symbol, ts)`）；
迁移后必须并存，且既有行一行不丢、动手前先有备份文件。
"""
import json
import sqlite3
import types
from datetime import datetime

import pytest

from paper_trading.data.db_manager import DataDBManager

OLD_DDL = """CREATE TABLE minute_bars (symbol TEXT NOT NULL, ts TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume REAL DEFAULT 0, amount REAL DEFAULT 0, adjust TEXT DEFAULT 'raw',
    period TEXT DEFAULT '5', PRIMARY KEY (symbol, ts))"""


def make_legacy_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(OLD_DDL)
    conn.executemany("INSERT INTO minute_bars VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


LEGACY_ROWS = [("000792", f"2026-10-28T{h:02d}:{m:02d}", 22.0, 22.1, 21.9, 22.05,
                1000.0, 22000.0, "raw", "5")
               for h, m in [(9, 35), (9, 40), (9, 45), (15, 0)]] + \
              [("600519", "2026-10-28T09:35", 1349.0, 1350.0, 1345.0, 1348.0,
                500.0, 674000.0, "raw", "5")]


def test_migration_keeps_rows_and_upgrades_pk(tmp_path):
    p = tmp_path / "data.db"
    make_legacy_db(p, LEGACY_ROWS)
    db = DataDBManager(p)                                  # 打开即迁移

    with db._connect() as conn:
        pk = [r["name"] for r in conn.execute("PRAGMA table_info(minute_bars)") if r["pk"]]
    assert pk == ["symbol", "ts", "period"]
    assert db.minute_count("000792") == 4 and db.minute_count("600519") == 1
    got = db.get_minute_bars("000792", period="5")
    assert [r["ts"] for r in got] == ["2026-10-28T09:35", "2026-10-28T09:40",
                                       "2026-10-28T09:45", "2026-10-28T15:00"]
    assert got[0]["adjust"] == "raw"


def test_migration_writes_backup_file_first(tmp_path):
    p = tmp_path / "data.db"
    make_legacy_db(p, LEGACY_ROWS)
    DataDBManager(p)
    bak = tmp_path / "data.db.pre-t35.bak"
    assert bak.exists() and bak.stat().st_size > 0
    # 备份里必须还是**旧结构**（能回滚），且行数不丢
    with sqlite3.connect(bak) as c:
        assert c.execute("SELECT COUNT(*) FROM minute_bars").fetchone()[0] == 5
        pk = [r[1] for r in c.execute("PRAGMA table_info(minute_bars)") if r[5]]
    assert pk == ["symbol", "ts"]


def test_migration_is_idempotent(tmp_path):
    p = tmp_path / "data.db"
    make_legacy_db(p, LEGACY_ROWS)
    DataDBManager(p)
    bak = tmp_path / "data.db.pre-t35.bak"
    first_mtime = bak.stat().st_mtime_ns
    DataDBManager(p)                                        # 再开一次不重复迁移
    assert bak.stat().st_mtime_ns == first_mtime
    assert db_ok(p) and db_ok(p)


def db_ok(path) -> bool:
    with sqlite3.connect(path) as c:
        n = c.execute("SELECT COUNT(*) FROM minute_bars").fetchone()[0]
        pk = [r[1] for r in c.execute("PRAGMA table_info(minute_bars)") if r[5]]
    return n == 5 and pk == ["symbol", "ts", "period"]


def test_1min_and_5min_coexist_after_migration(tmp_path):
    """覆盖风险回归：同一时刻两种周期必须各自成行，不再互相覆盖。"""
    db = DataDBManager(tmp_path / "data.db")
    row = {"ts": "2026-10-28T09:35", "open": 1.0, "high": 2.0, "low": 0.5,
           "close": 1.5, "volume": 10.0, "amount": 100.0}
    db.upsert_minute_bars("000792", [{**row, "close": 1.11}], period="1")
    db.upsert_minute_bars("000792", [{**row, "close": 5.55}], period="5")
    assert [r["close"] for r in db.get_minute_bars("000792", period="1")] == [1.11]
    assert [r["close"] for r in db.get_minute_bars("000792", period="5")] == [5.55]
    assert len(db.get_minute_bars("000792")) == 2          # 不带 period 看全量


def test_upsert_still_idempotent_per_key(tmp_path):
    db = DataDBManager(tmp_path / "data.db")
    row = {"ts": "2026-10-28T09:35", "open": 1.0, "high": 2.0, "low": 0.5,
           "close": 1.5, "volume": 10.0, "amount": 100.0}
    db.upsert_minute_bars("000792", [row], period="5")
    db.upsert_minute_bars("000792", [{**row, "close": 1.9}], period="5")
    got = db.get_minute_bars("000792", period="5")
    assert len(got) == 1 and got[0]["close"] == 1.9         # 同键更新，不新增


def test_prune_and_coverage_after_migration(tmp_path):
    p = tmp_path / "data.db"
    make_legacy_db(p, LEGACY_ROWS)
    db = DataDBManager(p)
    assert db.minute_coverage("5")["rows"] == 5
    assert db.minute_days(period="5") == ["2026-10-28"]
    assert db.prune_minute_bars("2026-10-28", period="5") == 0
    assert db.prune_minute_bars("2026-10-29", period="5") == 5
    assert db.minute_coverage("5")["rows"] == 0


def test_fresh_db_already_has_new_pk(tmp_path):
    db = DataDBManager(tmp_path / "fresh.db")
    with db._connect() as conn:
        pk = [r["name"] for r in conn.execute("PRAGMA table_info(minute_bars)") if r["pk"]]
    assert pk == ["symbol", "ts", "period"]
    assert not (tmp_path / "fresh.db.pre-t35.bak").exists()  # 新库不需要备份


# ---------- T38 批复 ②：迁移必须留痕（打开即自动 ⇒ 没留痕就是无痕改动）----------
def test_migration_is_recorded(tmp_path):
    p = tmp_path / "data.db"
    make_legacy_db(p, LEGACY_ROWS)
    db = DataDBManager(p)
    assert len(db.migrations) == 1
    rec = db.migrations[0]
    assert rec["kind"] == "pk_rebuild" and rec["table"] == "minute_bars"
    assert rec["pk"] == ["symbol", "ts", "period"]
    assert rec["rows"] == 5 and rec["backup"] == "data.db.pre-t35.bak"
    assert rec["db"] == str(p) and "at" in rec


def test_second_open_and_fresh_db_record_nothing(tmp_path):
    """留痕只能有一次：重复打开不许再写 op_log（否则审计流水会被噪声淹掉）。"""
    p = tmp_path / "data.db"
    make_legacy_db(p, LEGACY_ROWS)
    assert len(DataDBManager(p).migrations) == 1
    assert DataDBManager(p).migrations == []
    assert DataDBManager(tmp_path / "fresh.db").migrations == []


def test_cli_helper_lands_one_op_log_row(tmp_path):
    """`log_data_migrations` 把迁移一行一条写进账户库 op_log（只写审计，不碰余额）。"""
    from paper_trading.broker.paper_broker import PaperBroker
    from paper_trading.cli import log_data_migrations

    data_db = DataDBManager(tmp_path / "d.db")          # 新库：没有迁移
    account = PaperBroker(db_path=str(tmp_path / "a.db"))
    ns = types.SimpleNamespace(data_db=data_db, broker=account)
    assert log_data_migrations(ns) == 0
    assert [o["action"] for o in account.get_op_log(10)] == []

    make_legacy_db(tmp_path / "legacy.db", LEGACY_ROWS)
    ns.data_db = DataDBManager(tmp_path / "legacy.db")
    assert log_data_migrations(ns) == 1
    rows = [o for o in account.get_op_log(10) if o["action"] == "data:migrate"]
    assert len(rows) == 1
    # op_log.params 是 JSON 文本（`get_op_log` 原样返回行），留痕必须自己可读
    assert json.loads(rows[0]["params"])["kind"] == "pk_rebuild"
    assert rows[0]["ok"] == 1
    assert log_data_migrations(ns) == 0                # 已迁完，不再重复留痕
