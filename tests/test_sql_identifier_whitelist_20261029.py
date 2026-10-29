"""P1-2（批8）：f-string 拼 SQL 的标识符白名单与字面量转义（2026-10-29）。

覆盖三类插值位：表名（`PRAGMA table_info({table})` / `SELECT COUNT(*) FROM {table}` /
`CREATE VIEW {table}` / `_src.{table}`）、列名（`ALTER TABLE ... ADD COLUMN {column}`、
`MAX({column})`）、列类型（`... {col_type}`），以及必须当字面量用的路径
（`read_parquet('{path}')`）。

注：`market_state.py:206` 与 `database.py:783` 的列名分别来自同一表达式内的
字面量字典和既有 `("pe","pb","roe")` 白名单，不是外部输入，本次不加防御
（给不可能发生的场景写校验，只会掩盖真问题）。
"""

import pytest

from src.models import database as db_mod
from src.utils import safe_col_type, safe_identifier, sql_quote

HOSTILE = ("a; DROP TABLE kline_daily", "x\"y", "a--b", "a b", "", "1abc",
           "kline_daily;--")


class TestHelpers:
    def test_accepts_legit_identifiers(self):
        for name in ("kline_daily", "close_qfq", "instrument_id", "_tmp1",
                     "financial_summary"):
            assert safe_identifier(name) == name

    @pytest.mark.parametrize("bad", HOSTILE)
    def test_rejects_hostile_identifiers(self, bad):
        with pytest.raises(ValueError):
            safe_identifier(bad)

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            safe_identifier(None)
        with pytest.raises(ValueError):
            safe_identifier(12345)

    def test_col_type_accepts_real_forms(self):
        for t in ("TEXT", "REAL", "INTEGER", "VARCHAR(20)", "INTEGER NOT NULL"):
            assert safe_col_type(t) == t

    @pytest.mark.parametrize("bad", ("TEXT; DROP TABLE x", "REAL --", "x)",
                                     "TEXT'x", ""))
    def test_col_type_rejects_injection(self, bad):
        with pytest.raises(ValueError):
            safe_col_type(bad)

    def test_sql_quote_doubles_single_quotes(self):
        assert sql_quote("a'b") == "a''b"
        assert sql_quote("plain/path/x.parquet") == "plain/path/x.parquet"
        # 典型注入样式：闭合字面量后再起一句
        assert sql_quote("x'); DROP VIEW y;--") == "x''); DROP VIEW y;--"


class TestDatabaseCallSites:
    def test_add_column_rejects_hostile_names(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_mod, "get_db_path",
                            lambda: str(tmp_path / "w.db"))
        db_mod.init_database()
        with db_mod.db_conn() as conn:
            for bad in HOSTILE:
                with pytest.raises(ValueError):
                    db_mod._add_column_if_not_exists(conn, bad, "c1", "TEXT")
                with pytest.raises(ValueError):
                    db_mod._add_column_if_not_exists(conn, "kline_daily", bad,
                                                     "TEXT")
            with pytest.raises(ValueError):
                db_mod._add_column_if_not_exists(conn, "kline_daily", "c1",
                                                 "TEXT; DROP TABLE x")

    def test_add_column_still_works_for_legit_input(self, tmp_path, monkeypatch):
        """白名单不能把正常迁移路径一起打死。"""
        monkeypatch.setattr(db_mod, "get_db_path",
                            lambda: str(tmp_path / "w2.db"))
        db_mod.init_database()
        with db_mod.db_conn() as conn:
            db_mod._add_column_if_not_exists(conn, "kline_daily", "extra_col",
                                             "REAL")
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(kline_daily)").fetchall()}
        assert "extra_col" in cols

    def test_hostile_names_never_reach_sqlite(self, tmp_path, monkeypatch):
        """被拦下的调用不得留下任何副作用（表/列没被建、原表还在）。"""
        monkeypatch.setattr(db_mod, "get_db_path",
                            lambda: str(tmp_path / "w3.db"))
        db_mod.init_database()
        with db_mod.db_conn() as conn:
            with pytest.raises(ValueError):
                db_mod._add_column_if_not_exists(
                    conn, "kline_daily", "c1", "REAL DEFAULT 0); DROP TABLE "
                                               "screening_result;--")
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' "
                "AND name='screening_result'").fetchone()["n"]
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(kline_daily)").fetchall()}
        assert n == 1, "被拦下的语句不得真的执行过 DROP"
        assert "c1" not in cols


class TestSnapshotCallSite:
    def test_table_stat_rejects_hostile_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_mod, "get_db_path",
                            lambda: str(tmp_path / "s.db"))
        db_mod.init_database()
        from src.research.snapshot import table_stat
        with pytest.raises(ValueError):
            table_stat("kline_daily; DROP TABLE kline_daily")
        # 正常表仍可取指纹
        st = table_stat("kline_daily")
        assert st.rows == 0
