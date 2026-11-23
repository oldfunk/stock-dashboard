"""T94：会话证据必须逐份留存（2026-11-19 一天两场会话，第一场被覆盖）。"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.integration.rq_engine.runner import evidence_paths  # noqa: E402
from paper_trading.utils import daily_check as dc  # noqa: E402


def test_evidence_paths_are_per_session_different():
    a_sess, a_latest = evidence_paths(Path("state"), "2026-11-19",
                                      datetime(2026, 11, 19, 9, 32, 5))
    b_sess, b_latest = evidence_paths(Path("state"), "2026-11-19",
                                      datetime(2026, 11, 19, 9, 47, 5))
    assert a_sess.endswith("evidence_2026-11-19_093205.json")
    assert b_sess.endswith("evidence_2026-11-19_094705.json")
    assert a_sess != b_sess                       # 两场会话各有各的现场
    assert a_latest == b_latest                   # 别名固定，供只读端直接读
    assert a_latest.endswith("evidence_2026-11-19.json")


def test_latest_evidence_prefers_alias(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    (d / "evidence_2026-11-19_093205.json").write_text(json.dumps({"n": 1}))
    (d / "evidence_2026-11-19_094705.json").write_text(json.dumps({"n": 2}))
    (d / "evidence_2026-11-19.json").write_text(json.dumps({"n": 3}))
    ev, src = dc.latest_evidence(d, "2026-11-19")
    assert ev == {"n": 3} and src == "evidence_2026-11-19.json"


def test_latest_evidence_falls_back_to_newest_session_file(tmp_path):
    """别名没写成（进程崩在会话中间）也要读得到最后一份，不能假装"今天没留痕"。"""
    d = tmp_path / "state"
    d.mkdir()
    (d / "evidence_2026-11-19_093205.json").write_text(json.dumps({"n": 1}))
    (d / "evidence_2026-11-19_094705.json").write_text(json.dumps({"n": 2}))
    # 昨天的文件不得被当成今天
    (d / "evidence_2026-11-18_150005.json").write_text(json.dumps({"n": 99}))
    ev, src = dc.latest_evidence(d, "2026-11-19")
    assert ev == {"n": 2} and src == "evidence_2026-11-19_094705.json"


def test_latest_evidence_reports_broken_json(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    (d / "evidence_2026-11-19.json").write_text("{not json")
    ev, src = dc.latest_evidence(d, "2026-11-19")
    assert ev is None and "解析失败" in src
