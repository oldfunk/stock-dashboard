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


class TestPolishMarket:
    def test_polish_passthrough(self, monkeypatch):
        from src import ai_queue
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

    def test_explain_market(self, db, monkeypatch):
        from src import ai_queue
        monkeypatch.setattr(
            "src.analyzer.ai_analyzer.AiAnalyzer.ask_raw",
            lambda self, prompt, system=None: ("震荡", "m",
                                               {"prompt_tokens": 5,
                                                "completion_tokens": 5}))
        out = ai_queue.explain_market("重点看成交量")
        assert out["answer"] == "震荡"
        assert out["usage"]["prompt_tokens"] == 5


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
