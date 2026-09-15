#!/usr/bin/env python3
"""上游月检 P2#8：检查 xbtlin/ai-berkshire 的 skills/ + tools/ 有无新增 commit。

只用 stdlib（subprocess/urllib/json/argparse），零 emoji。
研报目录永远不看（沿用 09-07 结论）。

原理：
1. `git ls-remote <repo> HEAD` 取上游最新 SHA（轻量，不 clone）。
2. 与本地记录（默认 docs/upstream-check.json）对比；一致则 ledger 记 no-op。
3. 不一致则调 GitHub compare API 取 base..head 的变更文件，
   只保留 skills/ + tools/ 前缀，有命中才输出研判提示。

用法：
  python scripts/check_upstream.py                 # 实查（需联网）
  python scripts/check_upstream.py --dry-run       # 离线：只报告本地记录状态
  python scripts/check_upstream.py --dry-run --head <sha>  # 离线对比指定 SHA
  python scripts/check_upstream.py --save          # 实查且把新 SHA 写回记录
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.request

UPSTREAM_REPO = "https://github.com/xbtlin/ai-berkshire.git"
UPSTREAM_API = "https://api.github.com/repos/xbtlin/ai-berkshire"
WATCH_PREFIXES = ("skills/", "tools/")
DEFAULT_RECORD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs",
    "upstream-check.json",
)


def get_remote_head(repo_url=UPSTREAM_REPO, runner=None):
    """返回上游 HEAD 的 SHA，失败返回 None（诚实报错，不编造）。"""
    run = runner or subprocess.run
    try:
        proc = run(
            ["git", "ls-remote", repo_url, "HEAD"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.split()[0]


def load_record(path=DEFAULT_RECORD):
    """读本地记录；文件缺失/损坏返回 None（视为从未检查过）。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        sha = data.get("sha")
        return sha if sha else None
    except (OSError, ValueError):
        return None


def save_record(path, sha):
    """写回本地记录（含检查日期）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "sha": sha,
        "checked_at": datetime.date.today().isoformat(),
        "repo": UPSTREAM_REPO,
        "watch": list(WATCH_PREFIXES),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_changed_paths(base, head, fetcher=None):
    """经 GitHub compare API 取 base..head 变更文件列表；失败返回 None。"""
    if fetcher is not None:
        return fetcher(base, head)
    url = "%s/compare/%s...%s" % (UPSTREAM_API, base, head)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
    except (OSError, ValueError):
        return None
    files = data.get("files")
    if files is None:
        return None
    return [f.get("filename", "") for f in files]


def relevant(paths):
    """只保留 skills/ + tools/ 前缀（研报目录永远不看）。"""
    return [p for p in paths if p.startswith(WATCH_PREFIXES)]


def check(remote_head, record_sha, fetcher=None):
    """纯逻辑：对比 SHA 并过滤相关路径。返回 dict 结果。"""
    if not remote_head:
        return {"status": "error", "message": "上游不可达（ls-remote 失败），本次不记结论，下月重查。"}
    if record_sha is not None and remote_head == record_sha:
        return {"status": "none", "head": remote_head,
                "message": "上游无新增（HEAD 未变），ledger 记 no-op。"}
    if record_sha is None:
        return {"status": "new", "head": remote_head, "paths": [],
                "message": "本地无记录，无法 diff；请人工核对上游 skills/ + tools/ 后建档。"}
    paths = fetch_changed_paths(record_sha, remote_head, fetcher=fetcher)
    if paths is None:
        return {"status": "error", "head": remote_head,
                "message": "compare API 失败，HEAD 已变但差异不明；请人工查看上游后建档。"}
    hit = relevant(paths)
    if not hit:
        return {"status": "none", "head": remote_head,
                "message": "HEAD 有变但全是研报/索引类，skills/ + tools/ 无新增，ledger 记 no-op。"}
    return {"status": "new", "head": remote_head, "paths": hit,
            "message": "skills/ + tools/ 有 %d 个文件变更，需人工研判是否融入：\n%s"
                       % (len(hit), "\n".join("  - " + p for p in hit))}


def main(argv=None):
    ap = argparse.ArgumentParser(description="上游 skills/tools 月检")
    ap.add_argument("--repo", default=UPSTREAM_REPO)
    ap.add_argument("--record", default=DEFAULT_RECORD)
    ap.add_argument("--head", default=None, help="离线对比用：指定上游 SHA，跳过 ls-remote")
    ap.add_argument("--dry-run", action="store_true", help="离线：不联网，只报告本地记录状态")
    ap.add_argument("--save", action="store_true", help="把本次 HEAD 写回记录文件")
    args = ap.parse_args(argv)

    record_sha = load_record(args.record)
    if args.dry_run and args.head is None:
        if record_sha:
            print("dry-run：本地记录 HEAD=%s，需联网才可判断有无新增。" % record_sha)
        else:
            print("dry-run：本地无记录，实查后加 --save 建档。")
        return 0

    remote_head = args.head or get_remote_head(args.repo)
    result = check(remote_head, record_sha)
    print(result["message"])
    if args.save and result.get("head"):
        save_record(args.record, result["head"])
        print("已写回记录：%s" % args.record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
