"""东财 F10 JSON 解析硬化回归测试（2026-10-25）。

背景：审计实证 300750 资产负债表数据接口返回 `\\xef\\xbb\\xbf<`（BOM + HTML），
旧 `_get_json` 直接 `json.loads(utf-8)` 抛 JSONDecodeError 且不重试，导致
单源静默失败、回填仍标 done（29 只最新年字段缺口的根因之一）。
"""

import gzip

import pytest

from src.collector import balance_sheet_fetcher as bsf


class _Resp:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self) -> bytes:
        return self.payload


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(bsf.time, "sleep", lambda *_: None)


def test_bom_prefixed_json_is_parsed(monkeypatch):
    monkeypatch.setattr(
        bsf.urllib.request, "urlopen",
        lambda req, timeout=15: _Resp(b'\xef\xbb\xbf{"data": [1]}'))
    assert bsf._get_json("http://x") == {"data": [1]}


def test_gzip_json_is_parsed(monkeypatch):
    body = gzip.compress(b'{"data": [2]}')
    monkeypatch.setattr(
        bsf.urllib.request, "urlopen",
        lambda req, timeout=15: _Resp(body))
    assert bsf._get_json("http://x") == {"data": [2]}


def test_html_then_json_retries_and_succeeds(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(1)
        if len(calls) == 1:
            return _Resp(b"<html>blocked</html>")
        return _Resp(b'{"data": [3]}')

    monkeypatch.setattr(bsf.urllib.request, "urlopen", fake_urlopen)
    assert bsf._get_json("http://x") == {"data": [3]}
    assert len(calls) == 2


def test_html_always_fails_after_bounded_retries(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(1)
        return _Resp(b"<!DOCTYPE html><html>risk</html>")

    monkeypatch.setattr(bsf.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ValueError):
        bsf._get_json("http://x", retries=2)
    assert len(calls) == 3  # 1 次 + 2 次重试，不无限重试


def test_empty_body_fails(monkeypatch):
    monkeypatch.setattr(
        bsf.urllib.request, "urlopen",
        lambda req, timeout=15: _Resp(b"   "))
    with pytest.raises(ValueError):
        bsf._get_json("http://x", retries=0)
