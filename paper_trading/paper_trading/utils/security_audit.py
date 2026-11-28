"""安全复核（B3）：只读检查 + 演练清单。

检查的是**可自动判定**的部分：
- secrets 文件是否被 git 忽略/未被跟踪；
- POSIX 下权限是否 ≤0600（Windows 无 POSIX mode，明确标 unverified）；
- admin_token 是否存在且长度足够；
- config 里是否误写 Key；
- 面板默认监听地址的风险提示。

攻防演练（无口令写接口、CSRF、跨实例接管、Key 泄露）需要人工按
`docs/security-review.md` 的步骤执行；本模块不替人做破坏性测试。
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Optional


def _check(cid: str, ok: Optional[bool], level: str, detail: str,
           fix: str = "") -> dict:
    return {"id": cid, "ok": ok, "level": level, "detail": detail,
            "fix": fix}


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                           text=True, timeout=15)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def audit(root: str | Path = ".", secrets_path: Optional[str] = None) -> dict:
    root = Path(root)
    from paper_trading.llm.secrets import secrets_path as _sp

    sp = Path(_sp(secrets_path))
    checks: list[dict] = []

    # 1) secrets 文件存在性与权限
    if not sp.exists():
        checks.append(_check("secrets-exists", None, "warn",
                             f"未配置 {sp.name}（未使用云端 Key 时可接受）"))
    else:
        mode = None
        try:
            mode = stat.S_IMODE(sp.stat().st_mode)
        except OSError:
            pass
        if os.name == "nt":
            checks.append(_check(
                "secrets-mode", None, "warn",
                "Windows 无 POSIX 0600；请确认文件 ACL 仅当前用户可读",
                "icacls secrets.local.json /inheritance:r /grant:r %USERNAME%:F"))
        else:
            ok = mode is not None and (mode & 0o077) == 0
            checks.append(_check(
                "secrets-mode", ok, "fail" if ok is False else "info",
                f"{sp} mode={oct(mode) if mode is not None else 'unknown'}",
                f"chmod 600 {sp}"))

    # 2) git 忽略/未跟踪
    rel = None
    try:
        rel = str(sp.resolve().relative_to(root.resolve()))
    except ValueError:
        rel = None
    if rel:
        if not (root / ".git").exists():
            checks.append(_check("secrets-gitignored", None, "warn",
                                 "不是 git 仓库，跳过忽略/跟踪检查",
                                 "在仓库根运行本命令"))
        else:
            rc, _ = _git(root, "check-ignore", "-q", rel)
            checks.append(_check("secrets-gitignored", rc == 0, "fail",
                                 f"{rel} {'已' if rc == 0 else '未'}被 git 忽略",
                                 "把 secrets.local.json 加入 .gitignore"))
            rc2, outp = _git(root, "ls-files", "--error-unmatch", rel)
            checks.append(_check("secrets-untracked", rc2 != 0, "fail",
                                 f"{rel} {'未被' if rc2 != 0 else '已'}跟踪",
                                 "git rm --cached secrets.local.json"))

    # 3) admin_token
    if sp.exists():
        from paper_trading.llm import load_secrets

        data = load_secrets(str(sp))
        tok = str(data.get("admin_token") or "")
        checks.append(_check("admin-token", len(tok) >= 32, "fail",
                             f"admin_token 长度 {len(tok)}（要求 ≥32）",
                             "重跑 llm config 生成新口令，删除旧 secrets 后重配"))
        prov = data.get("provider") or {}
        key = str(prov.get("api_key") or "")
        checks.append(_check("provider-key-local-only", True, "info",
                             f"provider={prov.get('preset') or '?'} "
                             f"key={'已配置' if key else '未配置'}（只存本地）"))

    # 4) config 里误写 Key / token
    cfg = root / "config.yaml"
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8", errors="replace")
        bad = []
        for ln in text.splitlines():
            s = ln.split("#", 1)[0].strip()
            if not s or ":" not in s:
                continue
            k, _, v = s.partition(":")
            if k.strip().lower() in ("api_key", "apikey", "admin_token",
                                     "password", "secret") and v.strip():
                bad.append(k.strip())
        checks.append(_check("config-no-secrets", not bad, "fail",
                             ("config.yaml 含疑似密钥字段：" + "、".join(bad))
                             if bad else "config.yaml 未写 Key/口令",
                             "把密钥移到 secrets.local.json（gitignored）"))

    # 5) 面板监听默认值（代码事实，不是运行态）
    checks.append(_check(
        "dashboard-bind-default", None, "warn",
        "面板 CLI 默认 --host 0.0.0.0（局域网可访问）。"
        "仅限本机时应显式 --host 127.0.0.1；不要暴露公网。",
        "加防火墙/反代鉴权；或 python -m paper_trading.dashboard --host 127.0.0.1"))

    fails = [c for c in checks if c["level"] == "fail" and c["ok"] is False]
    warns = [c for c in checks if c["level"] == "warn"]
    return {
        "ok": not fails,
        "root": str(root),
        "secrets_path": str(sp),
        "checks": checks,
        "summary": {"fail": len(fails), "warn": len(warns),
                    "pass": len([c for c in checks if c["ok"] is True])},
        "drill": "人工演练步骤见 docs/security-review.md",
        "note": "只读检查；Windows ACL/浏览器端 XSS 等需人工复核。",
    }
