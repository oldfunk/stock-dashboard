"""coverage diff helper：对比两份 `data coverage --json` 输出，列出降级/恢复。"""
import json, sys

def load(p):
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("data", {}).get("coverage", d.get("coverage", {}))

def src(v):
    if isinstance(v, dict):
        return v.get("source") or v.get("sources")
    return None

if len(sys.argv) != 3:
    print("usage: coverage_diff.py before.json after.json")
    sys.exit(2)
a, b = load(sys.argv[1]), load(sys.argv[2])
keys = sorted(set(a) | set(b))
for k in keys:
    sa, sb = src(a.get(k)), src(b.get(k))
    if sa != sb:
        print(f"{k}: {sa} -> {sb}")
