"""腾讯行情行解析：PE(TTM) 落列（2026-10-29，审计 N8①）。

parts[52]=市盈率TTM（2026-10-29 nec 实测 600519：动态 parts[39]=20.79 /
TTM parts[52]=19.01 / 静态 parts[53]=20.56，语义自洽）。
"""

from src.utils import parse_tc_line


def _tc_line(parts_overrides: dict[int, str]) -> str:
    parts = ["v"] * 60
    for i, v in parts_overrides.items():
        parts[i] = v
    return 'v_sh600519="' + "~".join(parts) + '"'


def test_parse_tc_line_extracts_pe_ttm():
    line = _tc_line({1: "贵州茅台", 2: "600519", 3: "1348.61",
                     39: "20.79", 52: "19.01"})
    q = parse_tc_line(line)
    assert q is not None
    assert q["pe"] == 20.79
    assert q["pe_ttm"] == 19.01


def test_parse_tc_line_short_line_pe_ttm_none():
    """字段不足 52 位的老格式行：pe_ttm 为 None，不崩（门经 _num 回退 pe）。"""
    parts = ["v"] * 45
    parts[1] = "x"
    parts[2] = "000001"
    parts[3] = "10.0"
    q = parse_tc_line('v_sz000001="' + "~".join(parts) + '"')
    assert q is not None
    assert q["pe_ttm"] is None
