"""T120：安全复核只读检查（B3）。"""
import json

from paper_trading.utils.security_audit import audit


def _secrets(tmp_path, token="x" * 40, key="sk-test"):
    p = tmp_path / "secrets.local.json"
    p.write_text(json.dumps({"admin_token": token,
                             "provider": {"preset": "deepseek",
                                          "api_key": key}}), encoding="utf-8")
    return p


def test_audit_passes_local_secrets(tmp_path):
    sp = _secrets(tmp_path)
    (tmp_path / "config.yaml").write_text("strategy:\n  active: general\n",
                                          encoding="utf-8")
    out = audit(tmp_path, secrets_path=str(sp))
    assert out["ok"] is True
    checks = {c["id"]: c for c in out["checks"]}
    assert checks["admin-token"]["ok"] is True
    assert checks["config-no-secrets"]["ok"] is True
    # 非 git 仓库：忽略检查只 warn，不 fail
    assert checks["secrets-gitignored"]["level"] == "warn"


def test_audit_fails_on_short_token_and_inline_key(tmp_path):
    sp = _secrets(tmp_path, token="short", key="")
    (tmp_path / "config.yaml").write_text(
        "llm:\n  api_key: sk-inline\n", encoding="utf-8")
    out = audit(tmp_path, secrets_path=str(sp))
    assert out["ok"] is False
    checks = {c["id"]: c for c in out["checks"]}
    assert checks["admin-token"]["ok"] is False
    assert checks["config-no-secrets"]["ok"] is False
    assert out["summary"]["fail"] >= 2
