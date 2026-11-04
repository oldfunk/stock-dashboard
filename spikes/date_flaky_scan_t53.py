"""T53 清单生成器：把"测试会随日历老化"这一族扫出来（只读，不改文件）。

判据（都是这仓自己踩过的，不是审美）：
- **RISK-A 老化**：同一个测试文件里既写死了绝对日期、又依赖墙钟
  （`date.today()`/`datetime.now()`/`time.time()`），且那个日期距真实今天 < 安全边界
  ⇒ 过 N 天 lag 跨过产品阈值，断言翻脸（实测两次：`test_mother_contract` 的
  `asof=2026-10-26` 对 3 自然日窗口，10-30 起由绿变红）。
- **RISK-B 周末陷阱**：写死的日期落在周六/周日，而该文件测的是交易日语义
  （`is_trading_day`/解冻/计划目标日）⇒ `today+10` 正好落周六那一类（T34 教训）。
- **SAFE**：日期是历史数据（距今 ≥ SAFE_AGE_DAYS）或文件不读墙钟。

用法：python spikes/date_flaky_scan_t53.py [--json out.json]
"""
from __future__ import annotations

import ast
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

#: 距真实今天不足这么多天的绝对日期，视为"会老化"（30 天覆盖一周切换 + 一个月的节假日）
RISK_WINDOW_DAYS = 30
#: 超过这个年纪的日期当作历史数据，不再算风险
SAFE_AGE_DAYS = 180

DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
CLOCK_RE = re.compile(r"date\.today\(\)|datetime\.now\(\)|time\.time\(\)|\.today\(\)")
TRADING_RE = re.compile(r"is_trading_day|next_trading_day|trading_days|unfreeze|解冻|交易日")


def _dates(text: str) -> list[tuple[str, int, date]]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for m in DATE_RE.finditer(line):
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            out.append((m.group(0), i, d))
    return out


def _docstring_nodes(tree: ast.Module) -> set:
    """模块/类/函数的 docstring 节点——那是叙述，不是会老化的数据。"""
    out = set()
    nodes = [tree] + [n for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                        ast.ClassDef))]
    for n in nodes:
        body = getattr(n, "body", [])
        if body and isinstance(body[0], ast.Expr)                 and isinstance(body[0].value, ast.Constant)                 and isinstance(body[0].value.value, str):
            out.add(id(body[0].value))
    return out


def ast_dates(path_or_text, is_text: bool = False) -> list[tuple[str, int, date]]:
    """只取**代码里的字符串字面量**中的日期（注释与 docstring 不算）。

    为什么用 AST 而不是逐行正则：`test_mother_contract.py` 的注释里合法地提到
    "2026-10-27 审查发现"，那是历史叙述不是会老化的数据；只有真正写进
    fixture/断言的日期才算风险。
    """
    text = path_or_text if is_text else Path(path_or_text).read_text(encoding="utf-8")
    tree = ast.parse(text)
    docs = _docstring_nodes(tree)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docs:
            continue
        for m in DATE_RE.finditer(node.value):
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            out.append((m.group(0), node.lineno, d))
    return out


def classify(path: Path, today: date) -> dict:
    text = path.read_text(encoding="utf-8")
    ds = ast_dates(text, is_text=True)
    reads_clock = bool(CLOCK_RE.search(text))
    trading = bool(TRADING_RE.search(text))
    aging = [(raw, ln, (d - today).days) for raw, ln, d in ds
             if abs((d - today).days) < RISK_WINDOW_DAYS]
    weekend = [(raw, ln, d.strftime("%a")) for raw, ln, d in ds
               if trading and d.weekday() >= 5 and abs((d - today).days) < SAFE_AGE_DAYS]
    risks = []
    if aging and reads_clock:
        risks.append("RISK-A 绝对日期 + 墙钟比较（会老化）")
    elif aging:
        risks.append("RISK-A′ 近今天绝对日期（本文件不读墙钟，风险较低但仍会老化）")
    if weekend:
        risks.append("RISK-B 写死日期落在周末而文件测交易日语义")
    label = (path.relative_to(ROOT).as_posix() if ROOT in path.parents else path.name)
    return {"file": label, "dates": len(ds),
            "reads_wall_clock": reads_clock, "trading_semantics": trading,
            "aging": [{"date": a, "line": b, "days_from_today": c} for a, b, c in aging],
            "weekend": [{"date": a, "line": b, "dow": c} for a, b, c in weekend],
            "risks": risks,
            "verdict": ("RISK" if risks else "SAFE")}


def main() -> int:
    today = date.today()
    rows = [classify(p, today) for p in sorted(TESTS.glob("test_*.py"))]
    risky = [r for r in rows if r["verdict"] == "RISK"]
    print(f"扫描日（真实今天）：{today}   文件：{len(rows)}   有风险：{len(risky)}")
    for r in risky:
        print(f"\n■ {r['file']}  {r['risks']}  日期数={r['dates']} 读墙钟={r['reads_wall_clock']}")
        for a in r["aging"][:8]:
            print(f"    :{a['line']:<5} {a['date']}  距今天 {a['days_from_today']:+d} 天")
        for w in r["weekend"][:4]:
            print(f"    :{w['line']:<5} {w['date']} 是 {w['dow']}（周末）")
    print("\n（说明：RISK-A′/RISK-B 只是候选。是否真会老化，看 "
          "`spikes/clock_shift_t53.py` 把墙钟拨快 N 天后的红名单。）")
    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.write_text(json.dumps({"today": today.isoformat(),
                                   "window_days": RISK_WINDOW_DAYS,
                                   "files": rows}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
