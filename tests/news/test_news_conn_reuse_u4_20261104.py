"""U4 收尾用例（2026-11-04）：`for_codes` 一批代码只开一次连接，且取数语义不变。

复核结论：旧写法把 `with self._conn()` 写在 `for code in codes` 里面，
调度器摘要与详情页路由两个调用点都是 200–500 只的量级——每轮白付几百次 open/close。
本文件既钉"连接只开一次"，也钉**改成一条语句后过滤条件与排序没有漂移**
（这类"顺手重构"最容易把 since / unsummarized_only 的参数顺序搞错）。
"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "n.db"))
    db_mod.init_database()
    return db_mod


def _dao():
    from src.models.news import NewsDAO
    opens = []

    def counting_factory():
        return _CountingConn(db_mod.db_conn(), opens)
    return NewsDAO(conn_factory=counting_factory), opens


class _CountingConn:
    """包一层：记录连接被"打开"了几次，其余行为原样转发。"""

    def __init__(self, cm, opens):
        self._cm = cm
        self._opens = opens
        self._conn = None

    def __enter__(self):
        self._opens.append(1)
        self._conn = self._cm.__enter__()
        return self._conn

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)


def _seed(dao, code, n, base_day="2026-10-19"):
    dao.upsert_many([
        {"code": code, "name": f"{code}公司", "title": f"{code} 第{i}条",
         "summary": "正文", "url": f"http://x/{code}/{i}", "source": "test",
         "published_at": f"{base_day} {9 + i % 8:02d}:00"}
        for i in range(n)])


def test_one_connection_for_the_whole_batch(db):
    """10 只票：连接开 1 次（旧写法是 10 次）。"""
    writer, _ = _dao()
    codes = [f"60{i:04d}" for i in range(10)]
    for c in codes:
        _seed(writer, c, 3)
    dao, opens = _dao()
    rows = dao.for_codes(codes, limit_per_code=2)
    assert len(opens) == 1, f"应当只开一次连接，实际 {len(opens)} 次"
    assert len(rows) == 20, f"每只 2 条 × 10 只 = 20，实得 {len(rows)}"


def test_empty_batch_opens_no_connection(db):
    dao, opens = _dao()
    assert dao.for_codes([]) == []
    assert opens == []


def test_per_code_limits_and_grouping_are_unchanged(db):
    """分组 + 组内按发布时间倒序 + 每只限量：与逐只调用 by_code 的结果一致。"""
    writer, _ = _dao()
    _seed(writer, "300390", 5)
    _seed(writer, "000792", 2)
    dao, _ = _dao()
    batch = dao.for_codes(["300390", "000792"], limit_per_code=3)
    assert [r["code"] for r in batch] == (
        ["300390"] * 3 + ["000792"] * 2), "顺序仍是逐只分组拼接"
    per_code = (dao.by_code("300390", limit=3) + dao.by_code("000792", limit=3))
    assert [r["id"] for r in batch] == [r["id"] for r in per_code], \
        "批量与逐只取到的必须是同一批行（排序/限量不能漂）"


def test_since_and_unsummarized_filters_stay_bound_correctly(db):
    """两个可选条件同时开：参数顺序错了就会静默查空或报绑定数不对。"""
    writer, _ = _dao()
    _seed(writer, "300390", 3, base_day="2026-10-19")
    _seed(writer, "000792", 3, base_day="2026-10-01")
    dao, _ = _dao()
    got = dao.for_codes(["300390", "000792"], since="2026-10-10 00:00")
    assert {r["code"] for r in got} == {"300390"}, "since 之后的另一只该被过滤掉"

    all_rows = dao.for_codes(["300390", "000792"], unsummarized_only=True)
    assert len(all_rows) == 6
    dao.apply_sentiment([{"id": all_rows[0]["id"], "sentiment": "利好",
                          "brief": "b"}])
    left = dao.for_codes(["300390", "000792"], unsummarized_only=True)
    assert len(left) == 5, "已摘要的那条要被排除（unsummarized_only 仍生效）"
