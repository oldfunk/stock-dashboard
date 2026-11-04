"""T43 架构地图的取证脚本：模块清单、import 实测依赖图、可达性、测试映射、重复实现候选。

只用标准库 AST，不装新依赖；跑一次输出一份 JSON，地图里的数字都从它来。
"""
import ast
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
PKG = ROOT / "paper_trading"
TESTS = ROOT / "tests"


def mod_name(p: Path) -> str:
    rel = p.relative_to(ROOT)
    parts = list(rel.parts)[:-1] + [p.stem]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def walk(pkg: Path):
    return sorted(pkg.rglob("*.py"))


def imports_of(p: Path) -> set:
    """本文件 import 到的 paper_trading.* 模块（AST 实测，不靠 grep 猜）。"""
    out = set()
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return out
    pkg = mod_name(p)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.module and n.module.startswith("paper_trading"):
                out.add(n.module)
                if n.level:                      # from . import x / from .sub import x
                    base = pkg.split(".")[:-1]
                    if n.module:
                        out.add(".".join(base + [n.module]))
                    else:
                        for a in n.names:
                            if a.name != "*":
                                out.add(".".join(base + [a.name]))
                    out.discard("")
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith("paper_trading"):
                    out.add(a.name)
    out.discard(pkg)
    return {m for m in out if (PKG / (m.split("paper_trading.")[-1].replace(".", "/"))).exists()
            or m == "paper_trading"}


def stats(p: Path) -> dict:
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src) if not src.startswith("\x00") else None
    funcs = classes = 0
    if tree:
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs += 1
            elif isinstance(n, ast.ClassDef):
                classes += 1
    return {"lines": len(src.splitlines()), "funcs": funcs, "classes": classes}


mods = {}
for p in walk(PKG):
    m = mod_name(p)
    try:
        st = stats(p)
    except Exception:                              # noqa: BLE001
        st = {"lines": len(p.read_text(encoding="utf-8", errors="replace").splitlines()),
              "funcs": 0, "classes": 0}
    mods[m] = {"module": m, "path": str(p.relative_to(ROOT)), **st,
               "imports": sorted(imports_of(p)),
               "entry": bool("__main__" in p.read_text(encoding="utf-8", errors="replace"))}

fan_in = defaultdict(int)
edges = []
for m, info in mods.items():
    for t in info["imports"]:
        if t in mods:
            fan_in[t] += 1
            edges.append([m, t])

test_files = sorted(TESTS.glob("test_*.py"))
test_blobs = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in test_files}


def defined_symbols(p: Path) -> list:
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    out = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not n.name.startswith("_") and len(n.name) >= 8:
                out.append(n.name)      # 只取"有辨识度"的名字，避免 main/run/load 误配
    return out


test_refs = defaultdict(set)
for m, i in mods.items():
    names = defined_symbols(ROOT / i["path"])
    if not names:
        continue
    for fname, blob in test_blobs.items():
        hits = [s for s in names if s in blob]
        if hits or m in blob:
            test_refs[m].update(hits[:4])

# 全仓引用计数（找"没人用"的候选）：别的文件里是否出现该模块路径或它的辨识度符号
repo_blobs = {}
for base in (PKG, TESTS, ROOT / "spikes"):
    if base.exists():
        for p in base.rglob("*.py"):
            repo_blobs[str(p.relative_to(ROOT))] = p.read_text(encoding="utf-8", errors="replace")
blobs_n = {k: v for k, v in repo_blobs.items()}
refs_to = {}
for m, i in mods.items():
    own = i["path"].replace("\\", "/")
    names = defined_symbols(ROOT / i["path"])
    n = 0
    who = []
    for f, blob in blobs_n.items():
        if f == own:
            continue
        if m in blob or f".{m.split('.')[-1]}" in blob or any(s in blob for s in names):
            n += 1
            if len(who) < 4:
                who.append(f)
    refs_to[m] = {"files": n, "sample": who}

never_referenced = sorted(m for m in mods if refs_to[m]["files"] == 0)
untested = sorted(m for m in mods
                  if defined_symbols(ROOT / mods[m]["path"]) and not test_refs.get(m))
by_package = defaultdict(lambda: {"modules": 0, "lines": 0})
for m, i in mods.items():
    key = m.replace("paper_trading.", "").split(".")[0] if m != "paper_trading" else "(根)"
    by_package[key]["modules"] += 1
    by_package[key]["lines"] += i["lines"]
leaf_only = sorted(m for m, i in mods.items() if fan_in[m] == 0 and not i["entry"])
orphans = sorted(m for m, i in mods.items()
                 if fan_in[m] == 0 and not i["entry"] and not i["imports"])

# 重复实现候选：同一概念在多个模块里各自实现
FAMILY = {
    "费用计算": ["def _calc_buy_cost", "def _calc_sell_cost", "commission_rate", "transfer_fee_rate",
              "class FeeModel"],
    "交易日历": ["def is_trading_day", "def load_holidays", "holidays", "trading_calendar"],
    "价格映射": ["def price_map", "last_price", "def current_price"],
    "股票池解析": ["def resolve_pool", "get_pool_symbols", "pool_from"],
    "心跳/健康": ["def touch", "def age", "def due", "heartbeat"],
    "CLI 装配": ["def build_bridge", "TradingBridge("],
}
dup = {}
for name, keys in FAMILY.items():
    hits = []
    for m, i in mods.items():
        src = (ROOT / i["path"]).read_text(encoding="utf-8", errors="replace")
        n = sum(src.count(k) for k in keys)
        if n:
            hits.append({"module": m, "hits": n})
    dup[name] = sorted(hits, key=lambda h: -h["hits"])

total_lines = sum(i["lines"] for i in mods.values())
try:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
except Exception:                                      # noqa: BLE001
    commit = ""
out = {"commit": commit, "modules": len(mods), "total_lines": total_lines,
       "edges": len(edges), "fan_in": dict(sorted(fan_in.items(), key=lambda kv: -kv[1])),
       "top_by_lines": sorted([{"module": m, "lines": i["lines"], "funcs": i["funcs"],
                                 "classes": i["classes"], "fan_in": fan_in[m]}
                               for m, i in mods.items()],
                              key=lambda x: -x["lines"])[:15],
       "entries": [m for m, i in mods.items() if i["entry"]],
       "leaf_only": leaf_only, "orphans": orphans,
       "untested_modules": untested,
       "never_referenced": never_referenced,
       "by_package": {k: v for k, v in sorted(by_package.items(),
                                              key=lambda kv: -kv[1]["lines"])},
       "tested_modules": len([m for m in mods if test_refs.get(m)]),
       "tests_count": len(list(TESTS.glob("test_*.py"))),
       "dup_candidates": dup}
(ROOT / "spikes").mkdir(exist_ok=True)
(ROOT / "spikes" / "arch_map_data.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in out.items() if k not in ("dup_candidates", "fan_in")},
                 ensure_ascii=False, indent=1)[:4000])
print("\n== fan-in top 12 ==")
for m, n in sorted(fan_in.items(), key=lambda kv: -kv[1])[:12]:
    print("  %-46s %s" % (m, n))
print("\n== 重复候选 ==")
for name, hits in dup.items():
    print("  %s: %s" % (name, [(h["module"], h["hits"]) for h in hits[:5]]))
