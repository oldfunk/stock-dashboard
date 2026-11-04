"""U2 收尾用例（2026-11-04）：DuckDB ATTACH 的库路径必须走字面量转义。

复核时确认 `parquet.py` 里三处 `read_parquet(...)` / `COPY ... TO '...'` 都过了
`_lit()`→`sql_quote`，**唯独 ATTACH 业务库这一处直接插值**。
本文件不需要 duckdb（假 con 记录语句即可），钉两件事：单引号被成对转义、
拼出来的语句里没有第二条可执行语句。
"""

import pytest


class FakeCon:
    def __init__(self, load_ok=True):
        self.sql = []
        self._load_ok = load_ok
        self._loads = 0

    def execute(self, sql):
        self.sql.append(sql)
        if sql == "LOAD sqlite;":
            self._loads += 1
            # 首次 LOAD 失败（扩展没装过）是正常路径，装完再 LOAD 就该成功
            if not self._load_ok and self._loads == 1:
                raise RuntimeError("扩展未安装")
        return self

    def fetchone(self):
        return (1,)


def _attach_sql(con):
    hits = [s for s in con.sql if s.startswith("ATTACH")]
    assert len(hits) == 1, f"应当只发一条 ATTACH，实得 {con.sql}"
    return hits[0]


def test_plain_path_is_quoted_unchanged():
    from src.research.parquet import _connect_source
    con = FakeCon()
    _connect_source(con, "G:/data/db/stock_dashboard.db")
    assert _attach_sql(con) == (
        "ATTACH 'G:/data/db/stock_dashboard.db'"
        " AS _src (TYPE SQLITE, READ_ONLY);")


def test_path_with_single_quote_is_escaped_not_truncated():
    """带 ' 的目录名原本会把语句截断成语法错误；转义后是一个合法字面量。"""
    from src.research.parquet import _connect_source
    con = FakeCon()
    _connect_source(con, r"G:/Users/O'Brien/db/x.db")
    assert _attach_sql(con) == (
        "ATTACH 'G:/Users/O''Brien/db/x.db'"
        " AS _src (TYPE SQLITE, READ_ONLY);")


def test_closing_quote_then_extra_statement_stays_inert():
    """篡改过的路径试图"关引号 + 追加语句"，转义后整段仍在字符串字面量里。"""
    from src.research.parquet import _connect_source
    con = FakeCon()
    evil = "x.db' ; SELECT * FROM secret; -- "
    _connect_source(con, evil)
    assert _attach_sql(con) == (
        "ATTACH 'x.db'' ; SELECT * FROM secret; -- '"
        " AS _src (TYPE SQLITE, READ_ONLY);"), "注入的引号必须被成对转义"


def test_extension_installed_when_load_fails_first_time():
    from src.research.parquet import _connect_source
    con = FakeCon(load_ok=False)
    _connect_source(con, "/tmp/a.db")
    assert con.sql[0] == "LOAD sqlite;"
    assert con.sql[1] == "INSTALL sqlite;"
    assert con.sql[2] == "LOAD sqlite;"
    assert con.sql[3].startswith("ATTACH '/tmp/a.db'")


@pytest.mark.parametrize("col,table", [("trade_date", "kline_daily")])
def test_exported_identifiers_still_go_through_the_whitelist(col, table):
    """同模块另三处的白名单不能因为这次统一形态被顺手改掉（守 P1-2 的既有结论）。"""
    from src.utils import safe_identifier
    assert safe_identifier(table, "表名") == table
    assert safe_identifier(col, "列名") == col
    with pytest.raises(Exception):
        safe_identifier("kline_daily; DROP TABLE x--", "表名")
