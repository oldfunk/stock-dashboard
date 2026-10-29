"""AI 队列 + 并发批量 + 润色/大盘 + 并发配置单测（analyzer 改动附单测）。"""
import time

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
    db_mod.init_database()
    return db_mod


def _seed_screening(db_mod, run_id="r1",
                    codes=("600519", "000858", "600887")):
    db_mod.RunLogDAO().start_run(run_id)
    db_mod.RunLogDAO().complete_run(run_id, 5527, len(codes), 0)
    with db_mod.db_conn() as conn:
        for c in codes:
            conn.execute(
                "INSERT INTO screening_result "
                "(run_id, run_date, code, name, score)"
                " VALUES (?,?,?,?,?)",
                (run_id, "2026-09-28", c, "名" + c, 90.0))


def _make_fake_analyzer(holder, ok=True):
    class _FakeAnalyzer:
        def __init__(self, cfg=None):
            self.model = "m-test"
            self._last_usage = {"prompt_tokens": 100,
                                "completion_tokens": 50,
                                "model": "m-test"}

        @property
        def configured(self):
            return ok

        def analyze_stock(self, stock, extra_instruction=None):
            holder.setdefault("extras", []).append(extra_instruction)
            return {"model": "m-test", "investment_strategy": "",
                    "trade_strategy": {}}
    return _FakeAnalyzer


class TestParallel:
    def test_all_ok_extra_passthrough(self, db, monkeypatch):
        from src import ai_queue
        holder = {}
        monkeypatch.setattr("src.analyzer.ai_analyzer.AiAnalyzer",
                            _make_fake_analyzer(holder))
        _seed_screening(db)
        stocks = [{"code": c, "name": "名" + c}
                  for c in ("600519", "000858", "600887")]
        progress = []
        ok, failed = ai_queue.analyze_batch_parallel(
            stocks, "r1", interval_seconds=0, max_workers=3,
            extra_instruction="重点看现金流",
            on_progress=lambda a, b, i: progress.append((a, b, i)))
        assert (ok, failed) == (3, 0)
        assert holder["extras"] == ["重点看现金流"] * 3
        assert len(progress) >= 3
        rows = db.AiAnalysisLogDAO().get_by_run("r1")
        assert len(rows) == 3
        assert sum(r["prompt_tokens"] for r in rows) == 300

    def test_unconfigured(self, db, monkeypatch):
        from src import ai_queue
        monkeypatch.setattr("src.analyzer.ai_analyzer.AiAnalyzer",
                            _make_fake_analyzer({}, ok=False))
        _seed_screening(db, codes=("600519", "000858"))
        stocks = [{"code": "600519"}, {"code": "000858"}]
        assert ai_queue.analyze_batch_parallel(
            stocks, "r1", interval_seconds=0) == (0, 2)

    def test_empty(self, db):
        from src import ai_queue
        assert ai_queue.analyze_batch_parallel([], "r1") == (0, 0)


class TestQueue:
    def test_cancel_queued(self):
        from src.ai_queue import AnalysisQueue
        q = AnalysisQueue(auto_start=False)
        t1 = q.submit("整轮", "all", [{"code": "a"}], "r1", 0, 2)
        t2 = q.submit("单股", "once", [{"code": "b"}], "r1", 0, 2)
        assert q.cancel(t1) is True
        assert q.cancel("q-nope") is False
        states = {t["id"]: t["status"] for t in q.snapshot()}
        assert states == {t1: "cancelled", t2: "queued"}
        assert all("stocks" not in t for t in q.snapshot())

    def test_runs_to_done(self, db, monkeypatch):
        from src import ai_queue
        order = []

        def _fake_batch(stocks, run_id, interval_seconds=60,
                        max_workers=2, extra_instruction=None,
                        on_progress=None):
            order.append([s["code"] for s in stocks])
            if on_progress:
                on_progress(len(stocks), 0, len(stocks))
            return len(stocks), 0
        monkeypatch.setattr(ai_queue, "analyze_batch_parallel", _fake_batch)
        q = ai_queue.AnalysisQueue()
        tid = q.submit("整轮", "all",
                       [{"code": "600519"}, {"code": "000858"}],
                       "r1", 0, 2)
        for _ in range(100):
            st = {t["id"]: t for t in q.snapshot()}
            if st[tid]["status"] == "done":
                break
            time.sleep(0.05)
        assert st[tid]["status"] == "done"
        assert st[tid]["done"] == 2
        assert order == [["600519", "000858"]]


    def test_execute_exception_keeps_thread_alive(self, db, monkeypatch):
        """回归 N12：任务异常不得让队列线程静默死亡。

        旧实现 `_execute` 的 import 与取字段在 try 之外、`_loop` 对 `_execute`
        无兜底 → 一次异常就杀掉 "ai-queue" daemon 线程：`_loop` 只挑 queued，
        队列从此不再工作，而任务状态停在 running，面板与 pipeline_progress
        一起显示"永远在跑"。
        """
        from src import ai_queue
        seen = []

        def _batch(stocks, run_id, interval_seconds=60, max_workers=2,
                   extra_instruction=None, on_progress=None):
            seen.append(run_id)
            if run_id == "boom":
                raise RuntimeError("模拟分析炸线程")
            return len(stocks), 0
        monkeypatch.setattr(ai_queue, "analyze_batch_parallel", _batch)

        q = ai_queue.AnalysisQueue()
        t_bad = q.submit("坏任务", "all", [{"code": "600519"}], "boom", 0, 2)
        t_ok = q.submit("好任务", "all", [{"code": "000858"}], "ok", 0, 2)
        states = {}
        for _ in range(200):
            states = {t["id"]: t for t in q.snapshot()}
            if (states.get(t_bad, {}).get("status") == "failed"
                    and states.get(t_ok, {}).get("status") == "done"):
                break
            time.sleep(0.05)

        assert states[t_bad]["status"] == "failed", "异常任务必须落 failed，不能永停 running"
        assert states[t_bad]["error"].startswith("RuntimeError"), "死因要留在任务上"
        assert states[t_ok]["status"] == "done", "线程必须活着并继续消费后续任务"
        assert q._thread.is_alive()
        assert seen == ["boom", "ok"]

    def test_exception_escaping_execute_keeps_thread_alive(self, db, monkeypatch):
        """N12 的主症状：异常发生在 `_execute` 的 try **之前**，只能由 `_loop` 兜住。

        旧实现把 import 与取字段放在 try 外，且 `_loop` 没有兜底 → 这类异常
        直接穿出线程。这里造一个缺 `run_id` 的任务：`_execute` 第一行就 KeyError，
        修复后必须由 `_loop` 落成 failed、记死因、线程继续存活。
        """
        from src import ai_queue
        q = ai_queue.AnalysisQueue(auto_start=False)
        tid = "qbad"
        with q._lock:
            q._tasks[tid] = {"id": tid, "name": "坏任务", "mode": "all",
                             "stocks": [], "interval": 0, "max_workers": 1,
                             "extra": None, "total": 0, "done": 0, "failed": 0,
                             "status": "queued", "created_at": ""}
        q.start()
        states = {}
        for _ in range(100):
            states = {t["id"]: t for t in q.snapshot()}
            if states.get(tid, {}).get("status") == "failed":
                break
            time.sleep(0.05)
        assert states[tid]["status"] == "failed", \
            "try 之外抛异常时，任务不能永远停在 running"
        assert "KeyError" in states[tid]["error"]
        assert q._thread.is_alive()

    def test_mark_failed_survives_progress_write_error(self, db, monkeypatch):
        """状态回写本身失败时只记日志，不外抛——否则又回到静默死亡。"""
        from src import ai_queue
        from src.models import database as db_mod

        class _BadProgress:
            def update(self, *a, **k):
                raise RuntimeError("database is locked")
        monkeypatch.setattr(db_mod, "PipelineProgressDAO", _BadProgress)

        q = ai_queue.AnalysisQueue(auto_start=False)
        task = {"id": "q1", "name": "整轮", "run_id": "r1", "status": "running"}
        q._mark_failed(task, ValueError("boom"))          # 不得外抛
        assert task["status"] == "failed"
        assert "boom" in task["error"]

        q._mark_failed(None, ValueError("无任务"))         # 空任务也不得炸


class TestPolishMarket:
    def test_polish_passthrough(self, monkeypatch):
        from src import ai_queue
        from src.llm_config import ENV_KEY
        monkeypatch.setenv(ENV_KEY, "k")
        holder = {}

        def _fake_raw(self, prompt, system=None):
            holder["prompt"] = prompt
            holder["system"] = system
            return ("1. 看现金流", "m", {})
        monkeypatch.setattr(
            "src.analyzer.ai_analyzer.AiAnalyzer.ask_raw", _fake_raw)
        assert ai_queue.polish_requirement("看看银行") == "1. 看现金流"
        assert "看看银行" in holder["prompt"]
        assert "拆解" in holder["system"] or "指令" in holder["system"]
        assert ai_queue.polish_requirement("  ") is None

    def test_market_empty(self, db):
        from src import ai_queue
        assert ai_queue.build_market_context() == "大盘数据暂无"

    def test_submit_job_lifecycle(self, db, monkeypatch):
        import time
        from src import ai_queue
        monkeypatch.setattr(
            "src.ai_queue.write_market_note",
            lambda requirement=None, today=None: {
                "journal_date": "2026-09-28", "title": "t", "chars": 10,
                "model": "m", "usage": {}})
        jid = ai_queue.submit_market_note("看看")
        assert jid.startswith("mn")
        st = {}
        deadline = time.time() + 5
        while time.time() < deadline:
            st = ai_queue.get_market_note_job(jid) or {}
            if st.get("status") == "done":
                break
            time.sleep(0.05)
        assert st.get("status") == "done"
        assert st.get("chars") == 10
        assert ai_queue.get_market_note_job("mn9999") is None


class TestConcurrency:
    def test_default_and_bounds(self, tmp_path):
        from src import llm_config
        lp = tmp_path / "local.yaml"
        assert llm_config.get_concurrency(lp) == 2
        assert llm_config.set_concurrency(3, lp) == 3
        assert llm_config.get_concurrency(lp) == 3
        assert llm_config.set_concurrency(5, lp) == 5
        for bad in (0, 6, "x"):
            try:
                llm_config.set_concurrency(bad, lp)
            except llm_config.LLMSetupError:
                pass
            else:
                raise AssertionError(f"应拒绝 {bad}")
        assert llm_config.get_concurrency(lp) == 5
