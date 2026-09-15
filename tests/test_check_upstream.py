"""check_upstream.py 单测：mock 有新增 / 无新增 / 仅研报变更 / 离线 dry-run。"""

import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, "scripts"))

from check_upstream import (  # noqa: E402
    check,
    get_remote_head,
    load_record,
    relevant,
    save_record,
)


def _write_record(tmp_path, sha):
    p = str(tmp_path / "upstream-check.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"sha": sha}, f)
    return p


def test_no_new_same_sha(tmp_path):
    """无新增：ls-remote SHA 与记录一致 → status none，不调 API。"""
    record = _write_record(tmp_path, "abc123")
    assert load_record(record) == "abc123"

    class Proc:
        returncode = 0
        stdout = "abc123\tHEAD\n"

    assert get_remote_head(runner=lambda *a, **k: Proc()) == "abc123"
    result = check("abc123", "abc123",
                   fetcher=lambda b, h: (_ for _ in ()).throw(AssertionError("不应调 API")))
    assert result["status"] == "none"
    assert "no-op" in result["message"]


def test_new_relevant_paths(tmp_path):
    """有新增：HEAD 变了且 skills/tools 命中 → status new，只列相关文件。"""
    record = _write_record(tmp_path, "base000")
    files = ["skills/quality-screen/SKILL.md", "tools/terminal_value.py",
             "research/2026-09-30-report.md"]
    result = check("head111", load_record(record),
                   fetcher=lambda b, h: files)
    assert result["status"] == "new"
    assert result["paths"] == ["skills/quality-screen/SKILL.md", "tools/terminal_value.py"]
    assert "report" not in result["message"]


def test_new_irrelevant_only_is_noop():
    """HEAD 变了但全是研报 → status none，ledger 记 no-op。"""
    result = check("head222", "base000",
                   fetcher=lambda b, h: ["research/a.md", "index/README.md"])
    assert result["status"] == "none"
    assert "no-op" in result["message"]


def test_unreachable_is_error_not_new():
    """上游不可达 → status error，不伪装成有新增/无新增。"""
    assert get_remote_head(runner=lambda *a, **k: (_ for _ in ()).throw(OSError("net"))) is None
    result = check(None, "base000")
    assert result["status"] == "error"


def test_relevant_prefix_and_save_roundtrip(tmp_path):
    """前缀过滤 + 记录写回可读回。"""
    assert relevant(["skills/a.md", "tools/b.py", "docs/c.md"]) == ["skills/a.md", "tools/b.py"]
    p = str(tmp_path / "d" / "upstream-check.json")
    save_record(p, "deadbeef")
    assert load_record(p) == "deadbeef"
    assert load_record(str(tmp_path / "missing.json")) is None
