"""`--json` 模式：日志必须走 stderr，stdout 保持纯 JSON（机器可解析）。"""
import logging
import sys

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_use_stderr_moves_handlers():
    from paper_trading.utils import logger as lg

    log = lg.get_logger("pt_test_stderr")
    lg.use_stderr()
    assert all(h.stream is sys.__stderr__ for h in log.handlers)


def test_get_logger_handler_stream_after_use_stderr():
    from paper_trading.utils import logger as lg

    lg.use_stderr()
    log = lg.get_logger("pt_test_stderr2")
    assert all(h.stream is sys.__stderr__ for h in log.handlers)
