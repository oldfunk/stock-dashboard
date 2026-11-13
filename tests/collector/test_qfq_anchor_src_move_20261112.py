"""批20-a：qfq 重锚 + Sina 双拉上移 src 层的**契约**测试（2026-11-12，不打网络）。

派工单要求"src 不得反向 import scripts"，并给了两个选项。本件选 **①逻辑下沉 src**
（`trading_calendar` / `price_model` 先例），不选 ②subprocess：subprocess 会把失败
吞成返回码、拿不到结构化 summary、也无法注入假 fetch 做单测。

这里钉的是"下沉之后不许出现的四种退化"：
1. 脚本侧偷偷留了第二份实现（两个口径来源）→ 用**同一对象**断言；
2. 库函数自带建表副作用（`trading_calendar` 先例明确不许）→ 未建表时必须报错，
   而薄 CLI 必须自己建表；
3. 默认 fetch 仍从 scripts 取（反向依赖换了个位置藏起来）；
4. 下沉后 src 路径本身不可用 → 直接走 src 重锚一遍，验镜像与列保护。

批6-b 的 10 条行为回归仍在 `test_refresh_qfq_anchor_20261029.py`，走脚本路径不变。
"""

import sqlite3

import pytest

from src.collector import qfq_anchor as qa
from src.collector import sina_price as sp
from src.models import database as db_mod

END = "2026-09-30"
INSIDE = ["2026-09-01", "2026-09-02", END]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "q.db"))
    db_mod.init_database()
    return db_mod


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """指向一个**没建过表**的空库路径（验"库函数不建表"）。"""
    path = tmp_path / "unbuilt.db"
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    return path


def _seed(code="600519"):
    db_mod.KlineDAO().upsert_many(code, [
        {"trade_date": d, "open": 90.0, "close": 100.0, "high": 110.0,
         "low": 80.0, "volume": 1000.0, "amount": 1e5, "turnover": 0.5,
         "close_raw": 120.0, "close_adj": 240.0} for d in INSIDE])


def _fake_fetch(scale=1.1):
    def fetch(code, start_date):
        return [{"trade_date": d, "open": 90.0 * scale, "close": 100.0 * scale,
                 "high": 110.0 * scale, "low": 80.0 * scale, "volume": 2000.0,
                 "amount": 2e5, "turnover": 0.6, "close_raw": 120.0,
                 "close_adj": 240.0} for d in INSIDE]
    return fetch


def test_script_reexports_are_the_same_objects_not_copies():
    """脚本侧只许 re-export：留第二份实现就会红（两个口径来源必然漂移）。"""
    from scripts import backfill_price_history as hist
    from scripts import refresh_qfq_anchor as cli
    for name in ("TASK", "window_end", "window_start", "pending_codes",
                 "existing_window_rows", "merge_with_existing", "refresh_code",
                 "run"):
        assert getattr(cli, name) is getattr(qa, name), f"qfq 脚本侧复制了 {name}"
    for name in ("_sina_symbol", "_sina_daily", "fetch_kline_sina_adjusted"):
        assert getattr(hist, name) is getattr(sp, name), f"price 脚本侧复制了 {name}"


def test_src_modules_do_not_import_scripts():
    """静态守卫之外的定点断言：这两个新模块自己也不许反向依赖。"""
    import pathlib
    import re
    pat = re.compile(r"^\s*(from|import)\s+scripts[\s.]", re.M)
    for mod in (qa, sp):
        text = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert not pat.search(text), f"{mod.__name__} 反向 import scripts"


def test_library_run_does_not_create_tables(fresh_db):
    """`trading_calendar` 先例：建表是 CLI/应用启动的职责，库函数不许有副作用。"""
    assert not fresh_db.exists() or fresh_db.stat().st_size == 0
    with pytest.raises(sqlite3.OperationalError):
        qa.run(days=30, sleep=0, fetch=_fake_fetch())
    with db_mod.db_conn() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "kline_daily" not in names, "库函数偷偷建了表"


def test_thin_cli_does_create_tables(fresh_db, monkeypatch, capsys):
    """反面：CLI 必须自己 init_database，否则脚本在新库上直接不可用。"""
    from scripts import refresh_qfq_anchor as cli
    monkeypatch.setattr("sys.argv", ["refresh_qfq_anchor", "--dry-run"])
    assert cli.main() == 0
    assert '"anchor": null' in capsys.readouterr().out
    with db_mod.db_conn() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "kline_daily" in names and "backfill_progress" in names


def test_default_fetch_comes_from_src_not_scripts(db, monkeypatch):
    """默认 fetch 必须是 src 的那一个；从 scripts 取就是反向依赖换个地方藏。"""
    # 先导入脚本模块，逼它把名字**早绑定**成原对象：否则"违规写法"会在 run 内部
    # 现场 import 脚本、刚好捡到 patch 后的值，用例假绿。
    from scripts import backfill_price_history  # noqa: F401
    _seed()
    seen = []
    monkeypatch.setattr(sp, "fetch_kline_sina_adjusted",
                        lambda code, start: seen.append((code, start))
                        or _fake_fetch()(code, start))
    s = qa.run(days=30, sleep=0)
    assert seen == [("600519", "2026-08-31")], "默认 fetch 没走 src.collector.sina_price"
    assert s["done"] == 1


def test_src_path_reanchors_and_mirrors_close_qfq(db):
    """下沉后 src 路径本身可用：窗口内重锚 + close_qfq 恒镜像 close。"""
    _seed()
    s = qa.run(days=30, codes=["600519"], sleep=0, fetch=_fake_fetch(1.1))
    assert (s["done"], s["partial"], s["anchor"]) == (1, 0, END)
    with db_mod.db_conn() as conn:
        r = conn.execute("SELECT * FROM kline_daily WHERE code='600519'"
                         " AND trade_date=?", (END,)).fetchone()
    assert r["close"] == pytest.approx(110.0)
    assert r["close_qfq"] == r["close"]
    assert r["close_raw"] == pytest.approx(120.0)


def test_src_path_keeps_column_protection(db):
    """列保护跟着一起下沉：抓取缺 open 不得把现网 90.0 抹成 NULL。"""
    _seed()

    def drop_open(code, start):
        rows = _fake_fetch(1.1)(code, start)
        for r in rows:
            r["open"] = None
        return rows

    qa.run(days=30, codes=["600519"], sleep=0, fetch=drop_open)
    with db_mod.db_conn() as conn:
        r = conn.execute("SELECT open, close, high FROM kline_daily"
                         " WHERE code='600519' AND trade_date=?", (END,)).fetchone()
    assert r["close"] == pytest.approx(110.0)
    assert r["open"] == pytest.approx(90.0), "缺值必须回退现值"
    assert r["high"] == pytest.approx(121.0)


def test_src_path_fail_closed_on_fetch_error(db):
    """fail-closed 跟着下沉：抛异常一行都不写，进度记 partial 且不写 covered。"""
    _seed()

    def boom(code, start):
        raise RuntimeError("Sina 抓取失败")

    s = qa.run(days=30, codes=["600519"], sleep=0, fetch=boom)
    assert (s["done"], s["partial"]) == (0, 1)
    with db_mod.db_conn() as conn:
        r = conn.execute("SELECT close FROM kline_daily WHERE code='600519'"
                         " AND trade_date=?", (END,)).fetchone()
        p = conn.execute("SELECT status, covered_report_date FROM backfill_progress"
                         " WHERE task='qfq_anchor' AND code='600519'").fetchone()
    assert r["close"] == pytest.approx(100.0), "半套数据不得覆盖"
    assert p["status"] == "partial" and p["covered_report_date"] is None


def test_sina_bj_guard_survives_the_move(db, monkeypatch):
    """能力边界守卫必须跟着搬过来（批13-d）：BJ 在打网络之前就抛。"""
    from src.collector.akshare_fetcher import SourceUnsupportedError
    calls = []
    monkeypatch.setattr(sp, "_sina_daily", lambda *a, **k: calls.append(a) or [])
    with pytest.raises(SourceUnsupportedError):
        sp.fetch_kline_sina_adjusted("920305", "2016-01-01")
    assert calls == []