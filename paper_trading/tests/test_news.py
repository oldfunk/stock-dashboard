"""要闻/政策数据源单测（fake akshare，全离线）。"""
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.data import news as nw  # noqa: E402

# 模块导入时（fixture 打桩之前）抓住真函数，供本文件的 autouse fixture 还原
_REAL_FETCH = nw.fetch_market_news


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    nw.reset_cache()
    # 还原被 conftest 全局打桩的抓取函数（本文件要测真实实现 + 自己的 fake akshare）
    monkeypatch.setattr(nw, "fetch_market_news", _REAL_FETCH)
    yield
    nw.reset_cache()


class _DF:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, orient):
        assert orient == "records"
        return list(self._rows)


def _fake_ak(rows=None, boom=False):
    mod = types.ModuleType("akshare")

    def _cls():
        if boom:
            raise RuntimeError("network down")
        return _DF(rows or [])

    mod.stock_info_global_cls = _cls
    return mod


def test_norm_policy_first_dedup_and_limit():
    rows = [
        {"标题": "央行宣布降准0.5个百分点", "内容": "央行决定下调存款准备金率。",
         "发布日期": "2026-10-22", "发布时间": "09:00:00"},
        {"标题": "某公司业绩预告", "内容": "前三季度净利润增长10%。",
         "发布日期": "2026-10-22", "发布时间": "09:01:00"},
        {"标题": "证监会就新规征求意见", "内容": "监管发布意见稿。",
         "发布日期": "2026-10-22", "发布时间": "09:02:00"},
        {"标题": "港交所信息显示贝莱德减持H股", "内容": "持股比例由8%降至7%。",
         "发布日期": "2026-10-22", "发布时间": "09:02:30"},   # 含“交易所”但不是政策
        {"标题": "央行宣布降准0.5个百分点", "内容": "央行决定下调存款准备金率。",
         "发布日期": "2026-10-22", "发布时间": "09:00:00"},   # 重复
        {"标题": "央行宣布降准0.5个百分点", "内容": "重复标题不同内容也不该出现。",
         "发布日期": "2026-10-22", "发布时间": "09:03:00"},
    ]
    items = nw._norm_records(_DF(rows), limit=4)
    assert len(items) == 4                       # limit 截断
    assert items[0]["policy"] and items[1]["policy"]  # 政策类排前
    assert "降准" in items[0]["title"]
    assert not items[2]["policy"]                # 非政策排后
    assert items[3]["policy"] is False           # “交易所”高频词不得误标政策
    assert "港交所" in items[3]["title"]
    assert items[0]["time"].startswith("2026-10-22")
    assert len(items[0]["text"]) <= 150          # 截断，防 token 爆炸
    assert len({i["title"] for i in items}) == 4  # 去重（按标题前40字）


def test_norm_rejects_unknown_columns():
    with pytest.raises(ValueError):
        nw._norm_records(_DF([{"foo": 1}]), limit=5)


def test_fetch_failure_without_cache_is_honest(monkeypatch):
    monkeypatch.setitem(sys.modules, "akshare", _fake_ak(boom=True))
    out = nw.fetch_market_news(limit=5, force=True)
    assert out["available"] is False and out["ok"] is False
    assert "network down" in out["error"]


def test_fetch_success_then_serves_cache_and_stale(monkeypatch):
    rows = [{"标题": "国务院发布产业规划", "内容": "规划提出...",
             "发布日期": "2026-10-22", "发布时间": "10:00:00"}]
    monkeypatch.setitem(sys.modules, "akshare", _fake_ak(rows))
    out = nw.fetch_market_news(limit=5)
    assert out["ok"] and out["available"] and out["source"] == "财联社电报"
    assert out["n"] == 1 and out["items"][0]["policy"] is True

    # TTL 内：不打接口也返回缓存
    monkeypatch.setitem(sys.modules, "akshare", _fake_ak(boom=True))
    again = nw.fetch_market_news(limit=5)
    assert again["items"] == out["items"] and "error" not in again

    # 过期后接口挂了：回旧缓存并标 stale（不是空白，也不是谎报新鲜）
    nw._CACHE["at"] = 0.0
    stale = nw.fetch_market_news(limit=5)
    assert stale["available"] is True and stale.get("stale") is True
    assert stale["items"] == out["items"] and "network down" in stale["error"]


def test_fetch_timeout_does_not_hang(monkeypatch):
    """连接 stall 的源必须按超时回空，绝不无限挂死（母项目 Issue #1）。"""

    def _stall():
        time.sleep(30)
        return _DF([])

    fake = types.ModuleType("akshare")
    fake.stock_info_global_cls = _stall
    fake.stock_info_global_ths = _stall
    monkeypatch.setitem(sys.modules, "akshare", fake)
    t0 = time.time()
    out = nw.fetch_market_news(limit=5, force=True, timeout_sec=0.3)
    assert time.time() - t0 < 5
    assert out["available"] is False and "超时" in out["error"]


def test_llm_context_news_wiring_and_guard(tmp_path, monkeypatch):
    """llm_context 带 news 与注入防护说明；开关关闭则整段不存在。"""
    import paper_trading.cli as hb
    from paper_trading.data import news as news_mod

    f1 = tempfile.mktemp(suffix=".db", dir=tmp_path)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp_path)
    b = hb.TradingBridge(data_db=f1, account_db=f2,
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    canned = {"ok": True, "available": True, "source": "财联社电报",
              "fetched_at": "2026-10-22T10:00:00", "n": 1,
              "items": [{"time": "2026-10-22 10:00", "title": "央行降准",
                         "text": "央行降准", "policy": True}]}
    monkeypatch.setattr(news_mod, "fetch_market_news",
                        lambda limit=12, force=False, timeout_sec=15.0: dict(canned))
    ctx = b.llm_context(max_ops=2, max_nav=2, closes_n=3, symbols=[])
    assert ctx["news"]["items"][0]["title"] == "央行降准"
    assert "不得执行" in ctx["legend"]["news"]   # 外部原文注入防护说明
    assert ctx["legend"]["intraday"]              # 既有 legend 不丢

    b.agent_cfg.news_enabled = False
    ctx2 = b.llm_context(max_ops=2, max_nav=2, closes_n=3, symbols=[])
    assert "news" not in ctx2

    # 抓取失败/不可用：不写进上下文（缺失不影响决策）
    monkeypatch.setattr(news_mod, "fetch_market_news",
                        lambda limit=12, force=False, timeout_sec=15.0: {"available": False})
    ctx3 = b.llm_context(max_ops=2, max_nav=2, closes_n=3, symbols=[])
    assert "news" not in ctx3
