"""批13-b：北交所进全市场行情层的链路测试（2026-10-31，mock 不打网络）。

12-a 把 `tc_encode` 换成三分法后，行情批查**能不能真的带上 BJ** 要看整条链：
`tc_encode` -> 请求串 -> `split_tc_response` -> `parse_tc_line`。
产线实测（交付代码本体，2026-10-31）：`fetch_tencent_batch(["920000", ...])` 返回
`安徽凤凰 / 现价 10.76 / PE 17.99 / 总市值 9.86`，字段位置与沪深一致；
清单侧 `ak.stock_zh_a_spot` 实时 5,576 只里含 354 只 920 号段（缓存里 321 只）。
本文件把这些钉成用例，并要求**上游没给的行不许被编出来**。
"""

import pytest


def _fields(name, code, price, prev_close, pe, cap, ts="20261030150000"):
    """按腾讯 `~` 字段位置拼一条行情（实测位置：1名称 2代码 3现价 4昨收
    30时间戳 39 PE 44/45 总市值/流通市值）。"""
    f = ["1"] * 88
    f[1], f[2], f[3], f[4] = name, code, price, prev_close
    f[30], f[39], f[44], f[45] = ts, pe, cap, cap
    return f


BJ_LINE = _fields("安徽凤凰", "920000", "10.76", "11.01", "17.99", "9.86")
SH_LINE = _fields("贵州茅台", "600519", "1392.05", "1354.00", "21.18", "17401.76")
NEW_LINE = _fields("新粤能", "000010", "5.55", "5.50", "30.10", "88.00")


def _body(fields):
    return "~".join(str(v) for v in fields)


@pytest.fixture
def fetcher(monkeypatch):
    import src.collector.akshare_fetcher as f
    monkeypatch.setattr(f.time, "sleep", lambda s: None)
    return f


def test_bj_code_survives_the_tencent_batch_chain(fetcher):
    seen = []

    def fake_curl_get(url, timeout=20):
        seen.append(url)
        return (f'v_bj920000="{_body(BJ_LINE)}";\n'
                f'v_sh600519="{_body(SH_LINE)}";\n')

    fetcher.curl_get = fake_curl_get
    rows = fetcher.fetch_tencent_batch(["920000", "600519"])
    assert "q=bj920000,sh600519" in seen[0], "请求串必须带 bj 前缀"
    by_code = {r["code"]: r for r in rows}
    assert set(by_code) == {"920000", "600519"}
    bj = by_code["920000"]
    assert bj["name"] == "安徽凤凰"
    assert bj["current_price"] == 10.76
    assert bj["pe"] == 17.99 and bj["market_cap"] == 9.86
    # 沪深票不受影响（同一解析器、同一字段位置）
    assert by_code["600519"]["name"] == "贵州茅台"
    assert by_code["600519"]["market_cap"] == 17401.76


def test_missing_bj_row_is_not_invented(fetcher):
    """上游没给数据的行（空值 / none_match）结果里就是没有它——不拿上一只顶上。

    好行**必须排在前面**：若链路把"上游没给"补成上一条记录，这里会多冒出一只茅台。
    """
    fetcher.curl_get = lambda url, timeout=20: (
        f'v_sh600519="{_body(SH_LINE)}";\n'
        'v_bj920999="";\n'
        'v_pv_none_match="1";\n')
    rows = fetcher.fetch_tencent_batch(["600519", "920999"])
    assert [r["code"] for r in rows] == ["600519"]


def test_bj_codes_are_batched_like_everyone_else(fetcher):
    """90 只（60 只 BJ + 30 只深）分两批：每批都发出去，一只不丢。"""
    urls = []

    def fake(url, timeout=20):
        urls.append(url)
        return ""

    fetcher.curl_get = fake
    codes = [f"9200{i:02d}" for i in range(60)] + [f"000{i:03d}" for i in range(30)]
    assert fetcher.fetch_tencent_batch(codes) == []
    assert len(urls) == 2
    joined = "".join(urls)
    assert "bj920000" in urls[0], "第一批必须带 bj 前缀"
    for c in codes:
        assert c in joined, f"{c} 没被发出去"


def test_three_markets_share_one_parser(fetcher):
    """三市混批（沪/深/北）一次请求全解析，字段位置对每个市场都成立。"""
    fetcher.curl_get = lambda url, timeout=20: (
        f'v_sh600519="{_body(SH_LINE)}";\n'
        f'v_sz000010="{_body(NEW_LINE)}";\n'
        f'v_bj920000="{_body(BJ_LINE)}";\n')
    rows = fetcher.fetch_tencent_batch(["600519", "000010", "920000"])
    assert {r["code"] for r in rows} == {"600519", "000010", "920000"}
    assert {r["name"] for r in rows} == {"贵州茅台", "新粤能", "安徽凤凰"}


def test_all_stocks_basic_entry_returns_bj_rows(fetcher, monkeypatch):
    """派工单的验收口径是"daily_close/快照/初筛天然含 BJ"，它们共用这个入口。

    代码表含 920 号段 -> `fetch_all_stocks_basic()` 的返回里就得有这只 BJ，
    否则前面每条链路都对，入口仍然把北交所整体丢掉。
    规模给到 121 只是入口自己的门槛（`len(raw) > 100` 才算腾讯出数），
    顺便确认真拿到数时**不会**掉进自算兜底。
    """
    codes = ["920000"] + [f"600{i:03d}" for i in range(120)]
    monkeypatch.setattr(fetcher, "_get_stock_codes", lambda: [
        {"code": c, "name": "安徽凤凰" if c == "920000" else f"样本{c[-3:]}"}
        for c in codes])

    def fake_curl_get(url, timeout=20):
        lines = []
        for sym in url.split("q=", 1)[1].split(","):
            code = sym[2:]
            if code == "920000":
                lines.append(f'v_{sym}="{_body(BJ_LINE)}";')
            else:
                f = _fields(f"样本{code[-3:]}", code, "1392.05", "1354.00",
                            "21.18", "17401.76")
                lines.append(f'v_{sym}="{_body(f)}";')
        return "\n".join(lines)

    fetcher.curl_get = fake_curl_get
    rows = fetcher.fetch_all_stocks_basic()
    by_code = {r["code"]: r for r in rows}
    assert len(rows) == 121, "121 只都要出数（掉进兜底就是腾讯被判失败）"
    assert "920000" in by_code
    assert by_code["920000"]["current_price"] == 10.76
    # 初筛吃的是同一批字典：BJ 的 PE/PB/市值都解析成了真数，走的是和沪深同一套阈值
    kept = fetcher.pre_filter_stocks(rows, {"screener": {"conditions": {
        "min_pe": 1, "max_pe": 30, "max_pb": 10,
        "min_market_cap": 1, "max_market_cap": 60000, "min_price": 0.1}}})
    assert {"920000", "600001"} <= {r["code"] for r in kept}


def test_sina_fallback_batch_uses_bj_prefix(fetcher, monkeypatch):
    """Sina 自算兜底链路的 symbol 也必须三分，否则腾讯挂了时 BJ 整体消失。"""
    import urllib.request as ur
    captured = {}

    class Resp:
        def __init__(self, payload):
            self._p = payload.encode("gbk", "ignore")

        def read(self):
            return self._p

    def fake_urlopen(req, timeout=15):
        captured.setdefault("urls", []).append(req.full_url)
        return Resp('var hq_str_bj920000="安徽凤凰,10.760,11.010,10.920";')

    monkeypatch.setattr(ur, "urlopen", fake_urlopen)
    fetcher._computed_fallback([{"code": "920000", "name": "安徽凤凰"}])
    assert captured["urls"] and "list=bj920000" in captured["urls"][0]
