"""P2-1 的静态守卫（批8，2026-10-29）：src 层不得新增反向 import scripts。

调度器原来写 `from scripts.fix_price_contract import backfill_trading_calendar`，
失败被 catch 成一条 warning → 交易日历静默停更（子项目按 asof 判旧会降级）。
函数已下沉 `src/collector/trading_calendar.py`，脚本侧保留 re-export。

本用例把"不许反向依赖"变成机器可判的约束，而不是靠 review 眼力。

**顺手发现：Hermes 的 P2-1 只列了 1 处，实际有 3 处**，另两处在 analyzer 里：
- `src/analyzer/ai_analyzer.py:554` `from scripts.verify_intrinsic import (...)`
  ——**不在 try 里**，脚本改名/挪动会直接 ImportError 打断分析路径；
- `src/analyzer/ai_analyzer.py:1593` `from scripts.verify_valuation import ...`
  ——包在"永不抛异常"的 try 里，失败会静默丢掉 `_valuation_check` 双源验算标记。

把 `verify_intrinsic` / `verify_valuation` 下沉是另一个量级的改动（两个验算模块
自带 CLI 与被 scripts/verify_* 用例共同引用），不在 P2-1 边界内，已回帖请主 AI
单独派。这里用**棘轮**记账：只允许已记录的这一份存量，新增或变多都会红。
"""

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
PATTERN = re.compile(r"^\s*(from|import)\s+scripts[\s.]")

# 存量豁免（file -> 允许条数）。任何超出都会失败；清零后请删掉本表。
GRANDFATHERED = {
    "analyzer/ai_analyzer.py": 2,   # 见上面两处行号；待主 AI 派工下沉验算模块
}


def _violations():
    by_file: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(SRC).as_posix()
        for lineno, line in enumerate(text.split("\n"), 1):
            if line.lstrip().startswith("#"):
                continue
            if PATTERN.match(line):
                by_file.setdefault(rel, []).append(f"{rel}:{lineno}: {line.strip()}")
    return by_file


def test_no_new_src_imports_of_scripts():
    by_file = _violations()
    unexpected = [v for rel, vs in by_file.items()
                  for v in vs if rel not in GRANDFATHERED]
    assert not unexpected, (
        "src 层不得反向依赖 scripts（把函数下沉到 src/，脚本侧 re-export）：\n  "
        + "\n  ".join(unexpected))
    grew = [f"{rel}: 实际 {len(vs)} 条 > 记账 {GRANDFATHERED[rel]} 条"
            for rel, vs in by_file.items()
            if rel in GRANDFATHERED and len(vs) > GRANDFATHERED[rel]]
    assert not grew, "存量豁免被变多了：\n  " + "\n  ".join(grew)
