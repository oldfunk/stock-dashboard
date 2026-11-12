"""流水线失败归属：本次失败不得记到历史僵尸 run 上（主动小件，2026-11-13）。

产线只读实测（2026-11-13）：``run_log`` 有 **18 条** ``status='running'`` /
``end_time IS NULL`` 的僵尸行，跨 2026-10-15 .. 2026-10-29（进程被杀时兜底没机会跑）。
而 ``run_daily_pipeline`` 的异常兜底是按"最新一条 running"归属错误的：

    SELECT run_id FROM run_log WHERE status='running' ORDER BY start_time DESC LIMIT 1

``run_collect_and_screen`` 原先把两个惰性 import 放在 ``start_run`` **之前**，
所以 ImportError（N12 记录过的真实故障形态）发生时本次 run 根本还没有行，兜底就把
当日错误记到某条历史僵尸上——既污染历史记录，又让当日失败无迹可查。

修法是把 ``start_run`` 提到惰性 import 之前：本次行先落地，兜底自然归属正确。
**不在本件里清扫那 18 条僵尸**——那是产线写入 + 可能要引入新 status 值，归母方裁定。
"""

import pytest

from src.models import database as db_mod

ZOMBIE_OLD = "20261015_153019"
ZOMBIE_NEW = "20261029_155017"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "runlog.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    # 两条僵尸，start_time 显式写成历史时刻：兜底按 start_time DESC 挑，
    # 所以"最新那条僵尸"（ZOMBIE_NEW）才是原本会被冤枉的那条。
    with db_mod.db_conn() as conn:
        for rid, ts in ((ZOMBIE_OLD, "2026-10-15T15:30:19"),
                        (ZOMBIE_NEW, "2026-10-29T15:50:17")):
            conn.execute(
                "INSERT INTO run_log (run_id, start_time, status) VALUES (?,?,'running')",
                (rid, ts))
    return path


def _rows():
    with db_mod.db_conn() as conn:
        return {r["run_id"]: dict(r) for r in conn.execute("SELECT * FROM run_log")}


def _assert_zombies_untouched(rows):
    for rid in (ZOMBIE_OLD, ZOMBIE_NEW):
        assert rows[rid]["status"] == "running", f"历史僵尸 {rid} 被本次失败污染：{rows[rid]}"
        assert rows[rid]["error_message"] is None, f"{rid} 被写上了当日错误"
        assert rows[rid]["end_time"] is None, f"{rid} 被本次失败收尾"


def test_import_error_before_start_run_does_not_blame_an_old_zombie(db, monkeypatch):
    """惰性 import 炸掉时，本次失败必须留下自己的行，而不是冤枉历史僵尸。

    用 delattr 制造真实的 ImportError（``from ... import run_collect_pipeline``
    取不到名字），这正是 N12 记录过的故障形态，不是人为构造的假异常。
    """
    import src.orchestrator as orch_mod
    from src.collector import akshare_fetcher

    monkeypatch.delattr(akshare_fetcher, "run_collect_pipeline")
    assert orch_mod.run_daily_pipeline({}) is False

    rows = _rows()
    _assert_zombies_untouched(rows)
    mine = [r for rid, r in rows.items() if rid not in (ZOMBIE_OLD, ZOMBIE_NEW)]
    assert len(mine) == 1, f"本次失败应当恰好留下 1 条自己的 run_log 行，实际={rows}"
    assert mine[0]["status"] == "failed"
    assert "run_collect_pipeline" in (mine[0]["error_message"] or "")


def test_upstream_failure_after_start_run_is_attributed_to_current_run(db, monkeypatch):
    """start_run 之后才炸（常见情形）：兜底仍须把错误记在本次 run 上。

    这条钉住"别把兜底整个删掉"——删了的话本次行会永远停在 running，
    等于每天新造一条僵尸。
    """
    import src.orchestrator as orch_mod
    from src.collector import akshare_fetcher

    def boom(config):
        raise RuntimeError("上游采集炸了")

    monkeypatch.setattr(akshare_fetcher, "run_collect_pipeline", boom)
    assert orch_mod.run_daily_pipeline({}) is False

    rows = _rows()
    _assert_zombies_untouched(rows)
    mine = [r for rid, r in rows.items() if rid not in (ZOMBIE_OLD, ZOMBIE_NEW)]
    assert len(mine) == 1, f"应当恰好 1 条本次行，实际={rows}"
    assert mine[0]["status"] == "failed"
    assert "上游采集炸了" in (mine[0]["error_message"] or "")
    assert mine[0]["end_time"] is not None