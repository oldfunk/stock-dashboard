"""锁隔离：事件复盘不占全局下单锁（否则 LLM 复盘会推迟盘中止损 1–3 分钟）。"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.cli import _event_lock_path, main  # noqa: E402


def test_event_lock_path_is_separate():
    p = _event_lock_path("paper_trading.lock")
    assert p == "paper_trading.event.lock"
    assert p != "paper_trading.lock"


def test_event_cli_uses_event_lock_not_global():
    src = inspect.getsource(main)
    start = src.index('agent_command == "event"')
    seg = src[start:start + 900]
    assert "_event_lock_path" in seg
    assert "with run_lock(args.lock_file)" not in seg


def test_run_event_review_does_not_take_global_lock():
    from paper_trading.agent.events import run_event_review

    assert "run_lock" not in inspect.getsource(run_event_review)
